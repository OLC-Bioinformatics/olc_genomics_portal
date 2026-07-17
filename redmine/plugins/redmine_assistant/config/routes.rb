# frozen_string_literal: true

get(
  'projects/:project_id/redmine_assistant',
  to: 'redmine_assistant#index',
  as: :project_redmine_assistant
)

post(
  'projects/:project_id/redmine_assistant/search',
  to: 'redmine_assistant#search',
  as: :search_project_redmine_assistant
)
