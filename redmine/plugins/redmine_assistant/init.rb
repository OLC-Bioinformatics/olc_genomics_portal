# frozen_string_literal: true

Redmine::Plugin.register :redmine_assistant do
  name 'Redmine Documentation Assistant'
  author 'CFIA OLC Bioinformatics'
  description 'Semantic search over OLC Redmine Automator documentation'
  version '0.2.0'

  permission :view_redmine_assistant,
            {
              redmine_assistant: [
                :index,
                :search,
                :feedback
              ]
            },
            require: :loggedin

  # Checked explicitly by the controller. An empty action mapping prevents
  # this permission from granting access to the assistant by itself.
  permission :view_internal_assistant_documentation,
             {},
             require: :member

  menu :project_menu,
      :redmine_assistant,
      { controller: 'redmine_assistant', action: 'index' },
      caption: :label_redmine_assistant,
      after: :wiki,
      param: :project_id,
      if: proc { |project|
        configured_project_identifier = ENV.fetch(
          'REDMINE_ASSISTANT_PROJECT_IDENTIFIER',
          ''
        ).to_s.strip

        configured_project_identifier.present? &&
          project.identifier == configured_project_identifier &&
          User.current.logged? &&
          User.current.allowed_to?(
            :view_redmine_assistant,
            project
          )
      }
end
