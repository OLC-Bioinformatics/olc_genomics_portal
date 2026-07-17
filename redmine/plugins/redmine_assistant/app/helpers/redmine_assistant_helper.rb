# frozen_string_literal: true

module RedmineAssistantHelper
  def redmine_assistant_score(score)
    number_to_percentage(
      score.to_f * 100,
      precision: 1
    )
  end

  def redmine_assistant_heading_path(heading_path)
    CGI.unescapeHTML(heading_path.to_s)
  end
end
