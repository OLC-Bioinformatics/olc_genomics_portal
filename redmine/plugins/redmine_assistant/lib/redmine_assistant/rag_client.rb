# frozen_string_literal: true

require 'json'
require 'net/http'
require 'uri'

module RedmineAssistant
  class Error < StandardError
    attr_reader :request_id

    def initialize(message, request_id: nil)
      super(message)
      @request_id = request_id
    end
  end

  class ConfigurationError < Error; end
  class ServiceError < Error; end
  class ResponseError < Error; end

  class RagClient
    DEFAULT_RAG_URL = 'http://rag:8001'
    DEFAULT_TIMEOUT_SECONDS = 15
    DEFAULT_RESULT_LIMIT = 5
    MAX_RESULT_LIMIT = 10
    FEEDBACK_RATINGS = %w[helpful unhelpful].freeze
    FEEDBACK_REASONS = %w[
      irrelevant_results
      missing_documentation
      unclear_documentation
      outdated_documentation
      insufficient_detail
      other
    ].freeze
    MAX_FEEDBACK_COMMENT_LENGTH = 1_000

    ACCESS_CONTEXTS = %w[
      standard
      internal
    ].freeze

    INTEGER_PATTERN = /\A[0-9]+\z/

    def retrieve(query:, access_context:, limit: nil)
      context = normalized_access_context(
        access_context
      )
      uri = retrieve_uri

      request = build_request(
        uri: uri,
        query: query,
        limit: validated_limit(limit),
        access_context: context
      )

      response = perform_request(
        uri,
        request
      )

      validate_http_response!(
        response
      )

      header_request_id = response[
        'X-Request-ID'
      ].to_s.presence

      body = parse_body(
        response,
        request_id: header_request_id
      )

      request_id = (
        body['request_id'].to_s.presence ||
        header_request_id
      )

      unless response.is_a?(Net::HTTPSuccess)
        error_code = body.dig(
          'error',
          'code'
        ).to_s.presence

        message = (
          "RAG service returned HTTP #{response.code}"
        )

        if error_code
          message += " (#{error_code})"
        end

        raise ServiceError.new(
          message,
          request_id: request_id
        )
      end

      validate_success_body!(
        body,
        request_id: request_id
      )

      body
    rescue JSON::ParserError
      raise ResponseError,
            'RAG service returned invalid JSON'
    rescue SocketError,
           IOError,
           SystemCallError,
           Timeout::Error,
           Net::OpenTimeout,
           Net::ReadTimeout
      raise ServiceError,
            'Could not connect to the RAG service'
    end

    def submit_feedback(request_id:, rating:, access_context:, reason: nil, comment: nil)
      context = normalized_access_context(access_context)
      feedback = validated_feedback(
        request_id: request_id,
        rating: rating,
        reason: reason,
        comment: comment
      )
      uri = feedback_uri
      request = Net::HTTP::Post.new(uri)
      request['Accept'] = 'application/json'
      request['Content-Type'] = 'application/json'
      request['Authorization'] = "Bearer #{trusted_service_token}"
      request[trusted_access_header] = context
      request.body = feedback.to_json

      response = perform_request(uri, request)
      validate_http_response!(response)
      header_request_id = response['X-Request-ID'].to_s.presence
      body = parse_body(response, request_id: header_request_id)
      response_request_id = body['request_id'].to_s.presence || header_request_id

      unless response.is_a?(Net::HTTPSuccess)
        error_code = body.dig('error', 'code').to_s.presence
        message = "RAG service returned HTTP #{response.code}"
        message += " (#{error_code})" if error_code
        raise ServiceError.new(message, request_id: response_request_id)
      end

      unless body['status'] == 'ok' && body['feedback'].is_a?(Hash)
        raise ResponseError.new(
          'RAG service returned an invalid feedback response',
          request_id: response_request_id
        )
      end

      body
    rescue JSON::ParserError
      raise ResponseError, 'RAG service returned invalid JSON'
    rescue SocketError,
           IOError,
           SystemCallError,
           Timeout::Error,
           Net::OpenTimeout,
           Net::ReadTimeout
      raise ServiceError, 'Could not connect to the RAG service'
    end

    private

    def normalized_access_context(access_context)
      context = access_context
                .to_s
                .strip
                .downcase

      return context if ACCESS_CONTEXTS.include?(
        context
      )

      raise ConfigurationError,
            'Invalid RAG access context'
    end

    def build_request(
      uri:,
      query:,
      limit:,
      access_context:
    )
      request = Net::HTTP::Post.new(uri)

      request['Accept'] = 'application/json'
      request['Content-Type'] = 'application/json'
      request['Authorization'] = (
        "Bearer #{trusted_service_token}"
      )
      request[trusted_access_header] = (
        access_context
      )

      request.body = {
        query: query,
        limit: limit
      }.to_json

      request
    end

    def perform_request(uri, request)
      timeout = timeout_seconds

      Net::HTTP.start(
        uri.hostname,
        uri.port,
        use_ssl: uri.scheme == 'https',
        open_timeout: timeout,
        read_timeout: timeout,
        write_timeout: timeout
      ) do |http|
        http.request(
          request
        )
      end
    end

    def validate_http_response!(response)
      return if response.is_a?(
        Net::HTTPResponse
      )

      raise ResponseError,
            'RAG service returned an invalid HTTP response'
    end

    def parse_body(response, request_id:)
      body = JSON.parse(
        response.body.to_s
      )

      return body if body.is_a?(Hash)

      raise ResponseError.new(
        'RAG service returned an invalid JSON document',
        request_id: request_id
      )
    end

    def validate_success_body!(body, request_id:)
      unless body['status'] == 'ok'
        raise ResponseError.new(
          'RAG service returned an invalid success status',
          request_id: request_id
        )
      end

      unless body['sources'].is_a?(Array)
        raise ResponseError.new(
          'RAG response sources must be an array',
          request_id: request_id
        )
      end

      unless ACCESS_CONTEXTS.include?(
        body['access_context'].to_s
      )
        raise ResponseError.new(
          'RAG response access context is invalid',
          request_id: request_id
        )
      end
    end

    def validated_feedback(request_id:, rating:, reason:, comment:)
      normalized_request_id = request_id.to_s.strip
      unless normalized_request_id.match?(
        /\A[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\z/i
      )
        raise ConfigurationError, 'Retrieval request ID is invalid'
      end

      normalized_rating = rating.to_s.strip.downcase
      unless FEEDBACK_RATINGS.include?(normalized_rating)
        raise ConfigurationError, 'Feedback rating is invalid'
      end

      normalized_reason = reason.to_s.strip.presence
      if normalized_rating == 'helpful'
        if normalized_reason
          raise ConfigurationError, 'Helpful feedback cannot include an unhelpful reason'
        end
      elsif !FEEDBACK_REASONS.include?(normalized_reason)
        raise ConfigurationError, 'Unhelpful feedback reason is invalid'
      end

      normalized_comment = comment.to_s.strip.presence
      if normalized_comment && normalized_comment.length > MAX_FEEDBACK_COMMENT_LENGTH
        raise ConfigurationError, 'Feedback comment is too long'
      end

      {
        request_id: normalized_request_id,
        rating: normalized_rating,
        reason: normalized_reason,
        comment: normalized_comment
      }
    end

    def feedback_uri
      service_uri('/api/v1/feedback')
    end

    def service_uri(path)
      base = ENV.fetch(
        'REDMINE_ASSISTANT_RAG_URL',
        DEFAULT_RAG_URL
      ).to_s.strip
      if base.empty?
        raise ConfigurationError, 'REDMINE_ASSISTANT_RAG_URL is empty'
      end

      uri = URI.parse("#{base.delete_suffix('/')}#{path}")
      unless uri.is_a?(URI::HTTP) && uri.host.present?
        raise ConfigurationError, 'REDMINE_ASSISTANT_RAG_URL is invalid'
      end
      uri
    rescue URI::InvalidURIError
      raise ConfigurationError, 'REDMINE_ASSISTANT_RAG_URL is invalid'
    end

    def retrieve_uri
      service_uri('/api/v1/retrieve')
    end

    def trusted_service_token
      token = ENV.fetch(
        'RAG_TRUSTED_SERVICE_TOKEN',
        ''
      ).to_s.strip

      if token.empty?
        raise ConfigurationError,
              'RAG_TRUSTED_SERVICE_TOKEN is not configured'
      end

      token
    end

    def trusted_access_header
      header = ENV.fetch(
        'RAG_TRUSTED_ACCESS_HEADER',
        'X-Redmine-Assistant-Access'
      ).to_s.strip

      unless header.match?(
        /\A[A-Za-z0-9-]+\z/
      )
        raise ConfigurationError,
              'RAG_TRUSTED_ACCESS_HEADER is invalid'
      end

      header
    end

    def timeout_seconds
      integer_environment_value(
        'REDMINE_ASSISTANT_TIMEOUT_SECONDS',
        DEFAULT_TIMEOUT_SECONDS,
        minimum: 1,
        maximum: 60
      )
    end

    def validated_limit(limit)
      return configured_result_limit if limit.nil?

      value = strict_integer(
        limit,
        name: 'Result limit'
      )

      unless value.between?(
        1,
        MAX_RESULT_LIMIT
      )
        raise ConfigurationError,
              'Result limit must be between 1 and 10'
      end

      value
    end

    def configured_result_limit
      integer_environment_value(
        'REDMINE_ASSISTANT_DEFAULT_LIMIT',
        DEFAULT_RESULT_LIMIT,
        minimum: 1,
        maximum: MAX_RESULT_LIMIT
      )
    end

    def integer_environment_value(
      name,
      default,
      minimum:,
      maximum:
    )
      raw_value = ENV.fetch(
        name,
        default.to_s
      )

      value = strict_integer(
        raw_value,
        name: name
      )

      unless value.between?(
        minimum,
        maximum
      )
        raise ConfigurationError,
              "#{name} must be between " \
              "#{minimum} and #{maximum}"
      end

      value
    end

    def strict_integer(value, name:)
      return value if value.is_a?(Integer)

      if value.is_a?(String)
        normalized = value.strip

        if normalized.match?(
          INTEGER_PATTERN
        )
          return Integer(
            normalized,
            10
          )
        end
      end

      raise ConfigurationError,
            "#{name} must be an integer"
    end
  end
end