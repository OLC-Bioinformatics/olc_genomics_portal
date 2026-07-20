# frozen_string_literal: true

require_relative '../../lib/redmine_assistant/rag_client'

class RedmineAssistantController < ApplicationController
  before_action :require_login
  before_action :find_project
  before_action :authorize

  helper :redmine_assistant

  MAX_QUERY_LENGTH = 2_000

  def index
    @query = ''
    @results = []
    @access_context = assistant_access_context
  end

  def search
    @query = params[:query].to_s.strip
    @results = []
    @access_context = assistant_access_context

    if (message = validation_error)
      flash.now[:error] = message
      return render :index, status: :unprocessable_content
    end

    body = rag_client.retrieve(
      query: @query,
      limit: requested_limit,
      access_context: @access_context
    )
    @rag_request_id = body['request_id'].to_s.presence
    @results = normalized_results(body.fetch('sources'))
    render :index
  rescue RedmineAssistant::Error => e
    log_assistant_error('Redmine Assistant search unavailable', e, :warn)
    flash.now[:error] = l(:error_redmine_assistant_unavailable)
    render :index, status: :service_unavailable
  rescue StandardError => e
    log_assistant_error('Unexpected Redmine Assistant failure', e, :error)
    flash.now[:error] = l(:error_redmine_assistant_unexpected)
    render :index, status: :internal_server_error
  end

  private

  def find_project
    @project = Project.find(params[:project_id])
  rescue ActiveRecord::RecordNotFound
    render_404
  end

  def assistant_access_context
    if User.current.allowed_to?(:view_internal_assistant_documentation, @project)
      'internal'
    else
      'standard'
    end
  end

  def validation_error
    if params.key?(:include_internal) || params.key?(:access_context)
      return l(:error_redmine_assistant_invalid_access_context)
    end
    return l(:error_redmine_assistant_blank_query) if @query.empty?
    if @query.length > MAX_QUERY_LENGTH
      return l(:error_redmine_assistant_query_too_long, maximum: MAX_QUERY_LENGTH)
    end
    return nil if requested_limit.nil?

    value = Integer(requested_limit, 10)
    return nil if value.between?(1, 10)

    l(:error_redmine_assistant_invalid_limit)
  rescue ArgumentError, TypeError
    l(:error_redmine_assistant_invalid_limit)
  end

  def requested_limit
    params[:limit].presence
  end

  def rag_client
    @rag_client ||= RedmineAssistant::RagClient.new
  end

  def normalized_results(results)
    unless results.is_a?(Array)
      raise RedmineAssistant::ResponseError, 'RAG sources must be an array'
    end

    results.filter_map do |result|
      next unless result.is_a?(Hash)
      normalized = {
        'rank' => result['rank'].to_i,
        'score' => result['score'].to_f,
        'chunk_key' => result['chunk_key'].to_s,
        'source_path' => result['source_path'].to_s,
        'source_url' => result['source_url'].to_s,
        'document_title' => result['document_title'].to_s,
        'heading_path' => result['heading_path'].to_s,
        'content' => result['excerpt'].to_s,
        'access_level' => result['access_level'].to_s
      }
      next unless result_allowed?(normalized)
      normalized
    end
  end

  def result_allowed?(result)
    internal = result['access_level'] == 'internal' ||
               result['source_path'].start_with?('internal_only/')

    if @access_context == 'standard' && internal
      Rails.logger.error(
        'Redmine Assistant discarded an internal result for a standard user'
      )
      return false
    end

    return true unless internal

    result['access_level'] == 'internal' &&
      result['source_path'].start_with?('internal_only/')
  end

  def log_assistant_error(prefix, error, level)
    request_id = error.respond_to?(:request_id) ? error.request_id : nil
    message = "#{prefix}: #{error.class}"
    message += " request_id=#{request_id}" if request_id.present?
    Rails.logger.public_send(level, message)
  end
end
