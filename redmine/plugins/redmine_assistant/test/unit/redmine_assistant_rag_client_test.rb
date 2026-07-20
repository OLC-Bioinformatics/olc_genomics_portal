# frozen_string_literal: true

require_relative '../test_helper'
require_relative '../../lib/redmine_assistant/rag_client'

class RedmineAssistantRagClientTest < ActiveSupport::TestCase
  def setup
    @client = RedmineAssistant::RagClient.new

    ENV.stubs(:fetch)
       .with(
         'REDMINE_ASSISTANT_RAG_URL',
         'http://rag:8001'
       )
       .returns(
         'http://rag:8001'
       )

    ENV.stubs(:fetch)
       .with(
         'RAG_TRUSTED_SERVICE_TOKEN',
         ''
       )
       .returns(
         'test-token'
       )

    ENV.stubs(:fetch)
       .with(
         'RAG_TRUSTED_ACCESS_HEADER',
         'X-Redmine-Assistant-Access'
       )
       .returns(
         'X-Redmine-Assistant-Access'
       )

    ENV.stubs(:fetch)
       .with(
         'REDMINE_ASSISTANT_TIMEOUT_SECONDS',
         '15'
       )
       .returns('15')

    ENV.stubs(:fetch)
       .with(
         'REDMINE_ASSISTANT_DEFAULT_LIMIT',
         '5'
       )
       .returns('5')
  end

  def test_internal_request_sends_trusted_headers
    response = successful_response(
      access_context: 'internal'
    )

    captured_request = nil

    @client
      .stubs(:perform_request)
      .with do |uri, request|
        captured_request = request

        uri.to_s == (
          'http://rag:8001/api/v1/retrieve'
        )
      end
      .returns(response)

    body = @client.retrieve(
      query: 'Merge',
      access_context: 'internal',
      limit: 3
    )

    assert_equal(
      'internal',
      body['access_context']
    )

    assert_equal(
      'Bearer test-token',
      captured_request['Authorization']
    )

    assert_equal(
      'internal',
      captured_request[
        'X-Redmine-Assistant-Access'
      ]
    )

    assert_equal(
      '/api/v1/retrieve',
      captured_request.path
    )

    request_body = JSON.parse(
      captured_request.body
    )

    assert_equal(
      'Merge',
      request_body['query']
    )

    assert_equal(
      3,
      request_body['limit']
    )
  end

  def test_standard_request_sends_standard_context
    response = successful_response(
      access_context: 'standard'
    )

    captured_request = nil

    @client
      .stubs(:perform_request)
      .with do |_uri, request|
        captured_request = request
        true
      end
      .returns(response)

    body = @client.retrieve(
      query: 'ConFindr',
      access_context: 'standard'
    )

    assert_equal(
      'standard',
      body['access_context']
    )

    assert_equal(
      'standard',
      captured_request[
        'X-Redmine-Assistant-Access'
      ]
    )

    request_body = JSON.parse(
      captured_request.body
    )

    assert_equal(
      5,
      request_body['limit']
    )
  end

  def test_missing_token_is_rejected
    ENV.stubs(:fetch)
       .with(
         'RAG_TRUSTED_SERVICE_TOKEN',
         ''
       )
       .returns('')

    @client
      .expects(:perform_request)
      .never

    assert_raises(
      RedmineAssistant::ConfigurationError
    ) do
      @client.retrieve(
        query: 'ConFindr',
        access_context: 'standard'
      )
    end
  end

  def test_invalid_access_context_is_rejected
    @client
      .expects(:perform_request)
      .never

    assert_raises(
      RedmineAssistant::ConfigurationError
    ) do
      @client.retrieve(
        query: 'Merge',
        access_context: 'administrator'
      )
    end
  end

  def test_integer_limit_is_accepted
    response = successful_response(
      access_context: 'standard'
    )

    captured_request = nil

    @client
      .stubs(:perform_request)
      .with do |_uri, request|
        captured_request = request
        true
      end
      .returns(response)

    @client.retrieve(
      query: 'ConFindr',
      access_context: 'standard',
      limit: 3
    )

    request_body = JSON.parse(
      captured_request.body
    )

    assert_equal(
      3,
      request_body['limit']
    )
  end

  def test_string_limit_is_accepted
    response = successful_response(
      access_context: 'standard'
    )

    captured_request = nil

    @client
      .stubs(:perform_request)
      .with do |_uri, request|
        captured_request = request
        true
      end
      .returns(response)

    @client.retrieve(
      query: 'ConFindr',
      access_context: 'standard',
      limit: '4'
    )

    request_body = JSON.parse(
      captured_request.body
    )

    assert_equal(
      4,
      request_body['limit']
    )
  end

  def test_fractional_limit_is_rejected
    @client
      .expects(:perform_request)
      .never

    assert_raises(
      RedmineAssistant::ConfigurationError
    ) do
      @client.retrieve(
        query: 'ConFindr',
        access_context: 'standard',
        limit: 1.5
      )
    end
  end

  def test_excessive_limit_is_rejected
    @client
      .expects(:perform_request)
      .never

    assert_raises(
      RedmineAssistant::ConfigurationError
    ) do
      @client.retrieve(
        query: 'ConFindr',
        access_context: 'standard',
        limit: 11
      )
    end
  end

  def test_invalid_json_is_safe
    response = raw_response(
      Net::HTTPOK,
      body: 'not-json'
    )

    @client
      .stubs(:perform_request)
      .returns(response)

    error = assert_raises(
      RedmineAssistant::ResponseError
    ) do
      @client.retrieve(
        query: 'ConFindr',
        access_context: 'standard'
      )
    end

    refute_includes(
      error.message,
      'not-json'
    )
  end

  def test_non_success_response_is_safe
    response = json_response(
      Net::HTTPForbidden,
      {
        status: 'error',
        request_id: 'request-403',
        error: {
          code: 'forbidden_access_context',
          message: 'Sensitive upstream text'
        }
      }
    )

    @client
      .stubs(:perform_request)
      .returns(response)

    error = assert_raises(
      RedmineAssistant::ServiceError
    ) do
      @client.retrieve(
        query: 'Merge',
        access_context: 'internal'
      )
    end

    assert_equal(
      'request-403',
      error.request_id
    )

    refute_includes(
      error.message,
      'Sensitive upstream text'
    )

    refute_includes(
      error.message,
      'test-token'
    )
  end

  def test_success_response_requires_sources_array
    response = json_response(
      Net::HTTPOK,
      {
        status: 'ok',
        request_id: 'request-123',
        access_context: 'standard',
        sources: 'not-an-array'
      }
    )

    @client
      .stubs(:perform_request)
      .returns(response)

    assert_raises(
      RedmineAssistant::ResponseError
    ) do
      @client.retrieve(
        query: 'ConFindr',
        access_context: 'standard'
      )
    end
  end

  def test_success_response_requires_valid_access_context
    response = json_response(
      Net::HTTPOK,
      {
        status: 'ok',
        request_id: 'request-123',
        access_context: 'administrator',
        sources: []
      }
    )

    @client
      .stubs(:perform_request)
      .returns(response)

    assert_raises(
      RedmineAssistant::ResponseError
    ) do
      @client.retrieve(
        query: 'ConFindr',
        access_context: 'standard'
      )
    end
  end

  def test_non_http_response_is_rejected
    @client
      .stubs(:perform_request)
      .returns(nil)

    assert_raises(
      RedmineAssistant::ResponseError
    ) do
      @client.retrieve(
        query: 'ConFindr',
        access_context: 'standard'
      )
    end
  end

  private

  def successful_response(access_context:)
    json_response(
      Net::HTTPOK,
      {
        status: 'ok',
        request_id: 'request-123',
        access_context: access_context,
        sources: []
      }
    )
  end

  def json_response(response_class, body)
    raw_response(
      response_class,
      body: body.to_json
    )
  end

  def raw_response(response_class, body:)
    code, message = response_status(
      response_class
    )

    response = response_class.new(
      '1.1',
      code,
      message
    )

    response.instance_variable_set(
      :@read,
      true
    )

    response[
      'X-Request-ID'
    ] = 'request-123'

    response.body = body

    response
  end

  def response_status(response_class)
    case response_class.name
    when 'Net::HTTPOK'
      [
        '200',
        'OK'
      ]
    when 'Net::HTTPForbidden'
      [
        '403',
        'Forbidden'
      ]
    else
      raise ArgumentError,
            "Unsupported test response class: " \
            "#{response_class.name}"
    end
  end
end