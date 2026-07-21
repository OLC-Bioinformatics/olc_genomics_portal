# frozen_string_literal: true

require_relative '../test_helper'

class RedmineAssistantControllerTest < Redmine::ControllerTest
  tests RedmineAssistantController

  fixtures(
    :projects,
    :users,
    :roles,
    :members,
    :member_roles
  )

  def setup
    @project = Project.find(1)
    @administrator = User.find(1)
    @project_member = User.find(2)

    @member_roles = @project_member
                    .memberships
                    .where(project_id: @project.id)
                    .flat_map(&:roles)
                    .uniq

    @member_roles.each do |role|
      role.add_permission!(:view_redmine_assistant)
      role.remove_permission!(
        :view_internal_assistant_documentation
      )
    end
  end

  def teardown
    @member_roles.each do |role|
      role.remove_permission!(:view_redmine_assistant)
      role.remove_permission!(
        :view_internal_assistant_documentation
      )
    end
  end

  def test_anonymous_user_is_redirected_to_login
    get(
      :index,
      params: {
        project_id: @project.identifier
      }
    )

    assert_response :redirect
    assert_redirected_to(
      signin_path(
        back_url: project_redmine_assistant_url(
          project_id: @project.identifier
        )
      )
    )
  end

  def test_administrator_can_open_assistant
    sign_in(@administrator)

    get(
      :index,
      params: {
        project_id: @project.identifier
      }
    )

    assert_response :success
    assert_select 'h2', text: 'Assistant'
    assert_select 'form'
    assert_select 'input[name="query"]'
    assert_select(
      'link[href*="redmine_assistant"][rel="stylesheet"]',
      count: 1
    )
  end

  def test_authorized_project_member_can_open_assistant
    sign_in(@project_member)

    get(
      :index,
      params: {
        project_id: @project.identifier
      }
    )

    assert_response :success
    assert_select 'h2', text: 'Assistant'
  end

  def test_project_member_without_permission_is_forbidden
    @member_roles.each do |role|
      role.remove_permission!(:view_redmine_assistant)
    end

    sign_in(@project_member)

    get(
      :index,
      params: {
        project_id: @project.identifier
      }
    )

    assert_response :forbidden
  end

  def test_blank_query_is_rejected
    sign_in(@project_member)

    post(
      :search,
      params: {
        project_id: @project.identifier,
        query: '   '
      }
    )

    assert_response :unprocessable_content
    assert_select '.flash.error'
    assert_select '.redmine-assistant-result', count: 0
  end

  def test_query_longer_than_limit_is_rejected
    sign_in(@project_member)

    post(
      :search,
      params: {
        project_id: @project.identifier,
        query: 'a' * 2_001
      }
    )

    assert_response :unprocessable_content
    assert_select '.flash.error'
    assert_select '.redmine-assistant-result', count: 0
  end

  def test_invalid_result_limit_is_rejected
    sign_in(@project_member)

    post(
      :search,
      params: {
        project_id: @project.identifier,
        query: 'How do I run ConFindr?',
        limit: 11
      }
    )

    assert_response :unprocessable_content
    assert_select '.flash.error'
    assert_select '.redmine-assistant-result', count: 0
  end

  def test_successful_search_renders_standard_results
    sign_in(@project_member)

    ENV.stubs(:fetch)
       .with(
         'REDMINE_ASSISTANT_DOCS_BASE_URL',
         ''
       )
       .returns(
         'https://docs.example.gc.ca/redmine'
       )

    client = mock

    client.expects(:retrieve)
          .with(
            query: 'Which automator detects plasmids?',
            limit: nil,
            access_context: 'standard'
          )
          .returns(
            rag_response(
              access_context: 'standard',
              sources: [standard_result]
            )
          )

    @controller
      .stubs(:rag_client)
      .returns(client)

    post(
      :search,
      params: {
        project_id: @project.identifier,
        query: 'Which automator detects plasmids?'
      }
    )

    assert_response :success
    assert_select '.redmine-assistant-result', count: 1
    assert_select '.redmine-assistant-score', text: '91.0%'
    assert_select(
      '.redmine-assistant-result h4',
      text: /MobSuite/
    )
    assert_select(
      '.redmine-assistant-content',
      text: /detects plasmids/
    )
    assert_select(
      '.redmine-assistant-result-footer a',
      text: 'analysis/mobsuite.md',
      count: 1
    ) do |links|
      assert_equal(
        'https://docs.example.gc.ca/redmine/analysis/mobsuite/',
        links.first['href']
      )
      assert_equal('_blank', links.first['target'])
      assert_equal(
        'noopener noreferrer',
        links.first['rel']
      )
    end

    assert_includes(
      response.body,
      'Which automator detects plasmids?'
    )
  end

  def test_standard_user_discards_unexpected_internal_results
    sign_in(@project_member)

    client = mock

    client.expects(:retrieve)
          .with(
            query: 'How do I use the merge workflow?',
            limit: nil,
            access_context: 'standard'
          )
          .returns(
            rag_response(
              access_context: 'standard',
              sources: [
                standard_result,
                internal_result
              ]
            )
          )

    @controller
      .stubs(:rag_client)
      .returns(client)

    post(
      :search,
      params: {
        project_id: @project.identifier,
        query: 'How do I use the merge workflow?'
      }
    )

    assert_response :success
    assert_select '.redmine-assistant-result', count: 1
    assert_includes(response.body, 'analysis/mobsuite.md')
    refute_includes(response.body, 'internal_only/merge.md')
    refute_includes(
      response.body,
      'Internal merge instructions'
    )
  end

  def test_user_with_internal_permission_receives_internal_results
    grant_internal_permission
    sign_in(@project_member)

    client = mock

    client.expects(:retrieve)
          .with(
            query: 'How do I use the merge workflow?',
            limit: nil,
            access_context: 'internal'
          )
          .returns(
            rag_response(
              access_context: 'internal',
              sources: [internal_result]
            )
          )

    @controller
      .stubs(:rag_client)
      .returns(client)

    post(
      :search,
      params: {
        project_id: @project.identifier,
        query: 'How do I use the merge workflow?'
      }
    )

    assert_response :success
    assert_select '.redmine-assistant-result', count: 1
    assert_includes(response.body, 'internal_only/merge.md')
    assert_includes(
      response.body,
      'Internal merge instructions'
    )

    # Internal documentation must not link to the public docs site.
    assert_select(
      '.redmine-assistant-result-footer a',
      count: 0
    )
    assert_select(
      '.redmine-assistant-result-footer code',
      text: 'internal_only/merge.md',
      count: 1
    )
  end

  def test_browser_access_context_is_rejected
    sign_in(@project_member)

    @controller
      .expects(:rag_client)
      .never

    post(
      :search,
      params: {
        project_id: @project.identifier,
        query: 'How do I use Merge?',
        access_context: 'internal'
      }
    )

    assert_response :unprocessable_content
    assert_select '.flash.error'
  end

  def test_browser_include_internal_is_rejected
    sign_in(@project_member)

    @controller
      .expects(:rag_client)
      .never

    post(
      :search,
      params: {
        project_id: @project.identifier,
        query: 'How do I use Merge?',
        include_internal: true
      }
    )

    assert_response :unprocessable_content
    assert_select '.flash.error'
  end

  def test_rag_connection_failure_returns_safe_error
    sign_in(@project_member)

    client = mock

    client.expects(:retrieve)
          .with(
            query: 'Which automator detects plasmids?',
            limit: nil,
            access_context: 'standard'
          )
          .raises(
            RedmineAssistant::ServiceError.new(
              'Sensitive internal RAG connection details',
              request_id: 'rag-request-789'
            )
          )

    @controller
      .stubs(:rag_client)
      .returns(client)

    post(
      :search,
      params: {
        project_id: @project.identifier,
        query: 'Which automator detects plasmids?'
      }
    )

    assert_response :service_unavailable
    assert_select '.flash.error'
    assert_includes(
      response.body,
      'temporarily unavailable'
    )
    refute_includes(
      response.body,
      'Sensitive internal RAG connection details'
    )
  end

  def test_invalid_results_structure_returns_safe_error
    sign_in(@project_member)

    client = mock

    client.expects(:retrieve)
          .with(
            query: 'Which automator detects plasmids?',
            limit: nil,
            access_context: 'standard'
          )
          .returns(
            {
              'status' => 'ok',
              'request_id' => 'rag-request-123',
              'access_context' => 'standard',
              'sources' => 'not-an-array'
            }
          )

    @controller
      .stubs(:rag_client)
      .returns(client)

    post(
      :search,
      params: {
        project_id: @project.identifier,
        query: 'Which automator detects plasmids?'
      }
    )

    assert_response :service_unavailable
    assert_select '.flash.error'
    assert_includes(
      response.body,
      'temporarily unavailable'
    )
    refute_includes(response.body, 'not-an-array')
  end

  def test_missing_project_returns_not_found
    sign_in(@administrator)

    get(
      :index,
      params: {
        project_id: 'project-that-does-not-exist'
      }
    )

    assert_response :not_found
  end

  def test_successful_search_renders_feedback_controls
    sign_in(@project_member)
    client = mock
    client.expects(:retrieve).returns(
      rag_response(access_context: 'standard', sources: [standard_result]).merge(
        'request_id' => '11111111-1111-4111-8111-111111111111'
      )
    )
    @controller.stubs(:rag_client).returns(client)

    post(
      :search,
      params: {
        project_id: @project.identifier,
        query: 'Which automator detects plasmids?'
      }
    )

    assert_response :success
    assert_select '.redmine-assistant-feedback', count: 1
    assert_select '.redmine-assistant-feedback-helpful', count: 1
    assert_select '.redmine-assistant-feedback-unhelpful', count: 1
  end

  def test_helpful_feedback_is_forwarded_with_trusted_context
    sign_in(@project_member)
    client = mock
    client.expects(:submit_feedback).with(
      request_id: '11111111-1111-4111-8111-111111111111',
      rating: 'helpful',
      reason: nil,
      comment: nil,
      access_context: 'standard'
    ).returns('status' => 'ok')
    @controller.stubs(:rag_client).returns(client)

    post(
      :feedback,
      params: {
        project_id: @project.identifier,
        request_id: '11111111-1111-4111-8111-111111111111',
        rating: 'helpful'
      }
    )

    assert_response :success
    assert_equal('ok', response.parsed_body['status'])
  end

  def test_feedback_rejects_browser_access_context
    sign_in(@project_member)
    @controller.expects(:rag_client).never

    post(
      :feedback,
      params: {
        project_id: @project.identifier,
        request_id: '11111111-1111-4111-8111-111111111111',
        rating: 'helpful',
        access_context: 'internal'
      }
    )

    assert_response :unprocessable_content
  end


  private

  def sign_in(user)
    @request.session[:user_id] = user.id
  end

  def grant_internal_permission
    @member_roles.each do |role|
      role.add_permission!(
        :view_internal_assistant_documentation
      )
    end
  end

  def rag_response(access_context:, sources:)
    {
      'status' => 'ok',
      'request_id' => 'rag-request-123',
      'access_context' => access_context,
      'sources' => sources
    }
  end

  def standard_result
    {
      'rank' => 1,
      'score' => 0.91,
      'chunk_key' => 'analysis/mobsuite.md::0000',
      'source_path' => 'analysis/mobsuite.md',
      'source_url' => 'analysis/mobsuite.md',
      'document_title' => 'MobSuite',
      'heading_path' => (
        'MobSuite &gt; What does it do?'
      ),
      'excerpt' => (
        'MobSuite detects plasmids in draft genome assemblies.'
      ),
      'access_level' => 'standard'
    }
  end

  def internal_result
    {
      'rank' => 2,
      'score' => 0.87,
      'chunk_key' => 'internal_only/merge.md::0000',
      'source_path' => 'internal_only/merge.md',
      'source_url' => 'internal_only/merge.md',
      'document_title' => 'Merge',
      'heading_path' => (
        'Merge &gt; How do I use it?'
      ),
      'excerpt' => 'Internal merge instructions.',
      'access_level' => 'internal'
    }
  end
  def test_anonymous_feedback_is_redirected_to_login
    post(
      :feedback,
      params: {
        project_id: @project.identifier,
        request_id: '11111111-1111-4111-8111-111111111111',
        rating: 'helpful'
      },
      as: :json
    )

    assert_response :redirect
  end
  def test_feedback_requires_assistant_permission
    @member_roles.each do |role|
      role.remove_permission!(:view_redmine_assistant)
    end

    sign_in(@project_member)

    @controller.expects(:rag_client).never

    post(
      :feedback,
      params: {
        project_id: @project.identifier,
        request_id: '11111111-1111-4111-8111-111111111111',
        rating: 'helpful'
      },
      as: :json
    )

    assert_response :forbidden
  end

end
