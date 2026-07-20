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

    def retrieve_uri
      base = ENV.fetch(
        'REDMINE_ASSISTANT_RAG_URL',
        DEFAULT_RAG_URL
      ).to_s.strip

      if base.empty?
        raise ConfigurationError,
              'REDMINE_ASSISTANT_RAG_URL is empty'
      end

      uri = URI.parse(
        "#{base.delete_suffix('/')}" \
        '/api/v1/retrieve'
      )

      unless uri.is_a?(URI::HTTP) &&
             uri.host.present?
        raise ConfigurationError,
              'REDMINE_ASSISTANT_RAG_URL is invalid'
      end

      uri
    rescue URI::InvalidURIError
      raise ConfigurationError,
            'REDMINE_ASSISTANT_RAG_URL is invalid'
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