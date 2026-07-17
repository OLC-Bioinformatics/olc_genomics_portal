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
    end
  end

  def teardown
    @member_roles.each do |role|
      role.remove_permission!(:view_redmine_assistant)
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
    @request.session[:user_id] = @administrator.id

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
    @request.session[:user_id] = @project_member.id

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

    @request.session[:user_id] = @project_member.id

    get(
      :index,
      params: {
        project_id: @project.identifier
      }
    )

    assert_response :forbidden
  end

  def test_blank_query_is_rejected
    @request.session[:user_id] = @administrator.id

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
    @request.session[:user_id] = @administrator.id

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

  def test_successful_search_renders_standard_results
    @request.session[:user_id] = @administrator.id

    rag_response = {
      'status' => 'ok',
      'results' => [
        standard_result
      ]
    }

    @controller
      .stubs(:call_rag_service)
      .with('Which automator detects plasmids?')
      .returns(rag_response)

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
      '.redmine-assistant-result-footer code',
      text: 'analysis/mobsuite.md'
    )

    assert_includes(
      response.body,
      'Which automator detects plasmids?'
    )
  end

  def test_internal_results_are_discarded
    @request.session[:user_id] = @administrator.id

    rag_response = {
      'status' => 'ok',
      'results' => [
        standard_result,
        internal_result
      ]
    }

    @controller
      .stubs(:call_rag_service)
      .with('How do I use the merge workflow?')
      .returns(rag_response)

    post(
      :search,
      params: {
        project_id: @project.identifier,
        query: 'How do I use the merge workflow?'
      }
    )
    assert_response :success
    assert_select '.redmine-assistant-result', count: 1

    assert_includes(
      response.body,
      'analysis/mobsuite.md'
    )

    refute_includes(
      response.body,
      'internal_only/merge.md'
    )

    refute_includes(
      response.body,
      'Internal merge instructions'
    )
  end

  def test_rag_connection_failure_returns_safe_error
    @request.session[:user_id] = @administrator.id

    error_class = RedmineAssistantController.const_get(
      :RedmineAssistantError
    )

    @controller
      .stubs(:call_rag_service)
      .with('Which automator detects plasmids?')
      .raises(
        error_class,
        'Sensitive internal RAG connection details'
      )

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
    @request.session[:user_id] = @administrator.id

    rag_response = {
      'status' => 'ok',
      'results' => 'not-an-array'
    }

    @controller
      .stubs(:call_rag_service)
      .with('Which automator detects plasmids?')
      .returns(rag_response)

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
      'not-an-array'
    )
  end

  def test_missing_project_returns_not_found
    @request.session[:user_id] = @administrator.id

    get(
      :index,
      params: {
        project_id: 'project-that-does-not-exist'
      }
    )

    assert_response :not_found
  end

  private

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
      'content' => (
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
      'content' => 'Internal merge instructions.',
      'access_level' => 'internal'
    }
  end
end
