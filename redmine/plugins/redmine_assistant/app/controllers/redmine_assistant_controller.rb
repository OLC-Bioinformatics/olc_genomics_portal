# frozen_string_literal: true

require 'json'
require 'net/http'
require 'uri'

class RedmineAssistantController < ApplicationController
  before_action :require_login
  before_action :find_project
  before_action :authorize

  helper :redmine_assistant

  DEFAULT_RAG_URL = 'http://rag:8001'
  DEFAULT_TIMEOUT_SECONDS = 15
  DEFAULT_RESULT_LIMIT = 5
  MAX_QUERY_LENGTH = 2_000

  def index
    @query = ''
    @results = []
  end

  def search
    @query = params[:query].to_s.strip
    @results = []

    validation_error = validate_query(@query)

    if validation_error
      flash.now[:error] = validation_error

      return render(
        :index,
        status: :unprocessable_content
      )
    end

    begin
      response_body = call_rag_service(@query)
      @results = standard_results(response_body.fetch('results', []))
    rescue RedmineAssistantError => e
      Rails.logger.warn(
        'Redmine Assistant search unavailable: ' \
        "#{e.class}: #{e.message}"
      )

      flash.now[:error] = l(:error_redmine_assistant_unavailable)

      return render(
        :index,
        status: :service_unavailable
      )
    rescue StandardError => e
      Rails.logger.error(
        'Unexpected Redmine Assistant failure: ' \
        "#{e.class}: #{e.message}"
      )

      flash.now[:error] = l(:error_redmine_assistant_unexpected)

      return render(
        :index,
        status: :internal_server_error
      )
    end

    render :index
  end

  private

  class RedmineAssistantError < StandardError
  end

  def find_project
    @project = Project.find(params[:project_id])
  rescue ActiveRecord::RecordNotFound
    render_404
  end

  def validate_query(query)
    if query.empty?
      return l(:error_redmine_assistant_blank_query)
    end

    if query.length > MAX_QUERY_LENGTH
      return l(
        :error_redmine_assistant_query_too_long,
        maximum: MAX_QUERY_LENGTH
      )
    end

    nil
  end

  def call_rag_service(query)
    uri = search_uri
    timeout_seconds = configured_timeout_seconds

    request = Net::HTTP::Post.new(uri)
    request['Accept'] = 'application/json'
    request['Content-Type'] = 'application/json'
    request.body = {
      query: query,
      limit: configured_result_limit
    }.to_json

    response = Net::HTTP.start(
      uri.hostname,
      uri.port,
      use_ssl: uri.scheme == 'https',
      open_timeout: timeout_seconds,
      read_timeout: timeout_seconds,
      write_timeout: timeout_seconds
    ) do |http|
      http.request(request)
    end

    unless response.is_a?(Net::HTTPSuccess)
      raise RedmineAssistantError,
            "RAG service returned HTTP #{response.code}"
    end

    parsed_body = JSON.parse(response.body)

    unless parsed_body.is_a?(Hash)
      raise RedmineAssistantError,
            'RAG service returned an invalid JSON document'
    end

    unless parsed_body['status'] == 'ok'
      raise RedmineAssistantError,
            'RAG service did not return a successful status'
    end

    parsed_body
  rescue JSON::ParserError => e
    raise RedmineAssistantError,
          "Could not parse RAG response: #{e.message}"
  rescue SocketError,
         IOError,
         SystemCallError,
         Timeout::Error,
         Net::OpenTimeout,
         Net::ReadTimeout => e
    raise RedmineAssistantError,
          "Could not connect to RAG service: #{e.message}"
  end

  def search_uri
    base_url = ENV.fetch(
      'REDMINE_ASSISTANT_RAG_URL',
      DEFAULT_RAG_URL
    ).to_s.strip

    if base_url.empty?
      raise RedmineAssistantError,
            'REDMINE_ASSISTANT_RAG_URL is empty'
    end

    URI.parse("#{base_url.delete_suffix('/')}/search")
  rescue URI::InvalidURIError => e
    raise RedmineAssistantError,
          "Invalid RAG service URL: #{e.message}"
  end

  def configured_timeout_seconds
    integer_environment_value(
      'REDMINE_ASSISTANT_TIMEOUT_SECONDS',
      DEFAULT_TIMEOUT_SECONDS,
      minimum: 1,
      maximum: 60
    )
  end

  def configured_result_limit
    integer_environment_value(
      'REDMINE_ASSISTANT_DEFAULT_LIMIT',
      DEFAULT_RESULT_LIMIT,
      minimum: 1,
      maximum: 10
    )
  end

  def integer_environment_value(
    name,
    default,
    minimum:,
    maximum:
  )
    raw_value = ENV.fetch(name, default.to_s)
    value = Integer(raw_value, 10)

    unless value.between?(minimum, maximum)
      raise RedmineAssistantError,
            "#{name} must be between #{minimum} and #{maximum}"
    end

    value
  rescue ArgumentError
    raise RedmineAssistantError,
          "#{name} must be an integer"
  end

  def standard_results(results)
    unless results.is_a?(Array)
      raise RedmineAssistantError,
            'RAG results must be an array'
    end

    results.filter_map do |result|
      next unless result.is_a?(Hash)

      source_path = result['source_path'].to_s
      access_level = result['access_level'].to_s

      if access_level != 'standard' ||
         source_path.start_with?('internal_only/')
        Rails.logger.error(
          'Redmine Assistant discarded an internal search result'
        )
        next
      end

      normalized_result(result)
    end
  end

  def normalized_result(result)
    {
      'rank' => result['rank'].to_i,
      'score' => result['score'].to_f,
      'chunk_key' => result['chunk_key'].to_s,
      'source_path' => result['source_path'].to_s,
      'document_title' => result['document_title'].to_s,
      'heading_path' => result['heading_path'].to_s,
      'content' => result['content'].to_s,
      'access_level' => 'standard'
    }
  end
end
