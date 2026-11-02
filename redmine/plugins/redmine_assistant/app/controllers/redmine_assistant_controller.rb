# frozen_string_literal: true

require_relative '../../lib/redmine_assistant/rag_client'

class RedmineAssistantController < ApplicationController
  before_action :require_login
  before_action :find_project
  before_action :require_assistant_project
  before_action :authorize, only: %i[index search]
  before_action :authorize_feedback, only: :feedback

  helper :redmine_assistant

  MAX_QUERY_LENGTH = 2_000
  MAX_FEEDBACK_COMMENT_LENGTH = 1_000
  FEEDBACK_RATINGS = %w[helpful unhelpful].freeze
  FEEDBACK_REASONS = %w[
    irrelevant_results
    missing_documentation
    unclear_documentation
    outdated_documentation
    insufficient_detail
    other
  ].freeze
  REQUEST_ID_PATTERN = /\A[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\z/i

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

  def feedback
    access_context = assistant_access_context
    feedback = validated_feedback_params

    rag_client.submit_feedback(
      request_id: feedback[:request_id],
      rating: feedback[:rating],
      reason: feedback[:reason],
      comment: feedback[:comment],
      access_context: access_context
    )

    render json: {
      status: 'ok',
      message: l(:notice_redmine_assistant_feedback_recorded)
    }
  rescue ActionController::ParameterMissing, ArgumentError => e
    Rails.logger.info("Invalid Redmine Assistant feedback: #{e.class}")
    render json: {
      status: 'error',
      message: l(:error_redmine_assistant_invalid_feedback)
    }, status: :unprocessable_content
  rescue RedmineAssistant::Error => e
    log_assistant_error('Redmine Assistant feedback unavailable', e, :warn)
    render json: {
      status: 'error',
      message: l(:error_redmine_assistant_feedback_unavailable)
    }, status: :service_unavailable
  rescue StandardError => e
    log_assistant_error('Unexpected Redmine Assistant feedback failure', e, :error)
    render json: {
      status: 'error',
      message: l(:error_redmine_assistant_feedback_unavailable)
    }, status: :internal_server_error
  end

  private

  def find_project
    @project = Project.find(params[:project_id])
  rescue ActiveRecord::RecordNotFound
    render_404
  end

  def authorize_feedback
    return true if User.current.allowed_to?(
      :view_redmine_assistant,
      @project
    )

    deny_access
  end

  def require_assistant_project
    return true if @project.identifier == assistant_project_identifier

    render_404
    false
  end

  def assistant_project_identifier
    ENV.fetch(
      'REDMINE_ASSISTANT_PROJECT_IDENTIFIER',
      ''
    ).to_s.strip
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

  def validated_feedback_params
    request_id = params.require(:request_id).to_s.strip
    rating = params.require(:rating).to_s.strip.downcase
    reason = params[:reason].to_s.strip.presence
    comment = params[:comment].to_s.strip.presence

    raise ArgumentError unless request_id.match?(REQUEST_ID_PATTERN)
    raise ArgumentError unless FEEDBACK_RATINGS.include?(rating)
    raise ArgumentError if comment && comment.length > MAX_FEEDBACK_COMMENT_LENGTH
    if rating == 'helpful'
      raise ArgumentError if reason
    else
      raise ArgumentError unless FEEDBACK_REASONS.include?(reason)
    end
    if params.key?(:include_internal) || params.key?(:access_context)
      raise ArgumentError
    end

    {
      request_id: request_id,
      rating: rating,
      reason: reason,
      comment: comment
    }
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
