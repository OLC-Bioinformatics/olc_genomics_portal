# frozen_string_literal: true

require 'cgi'
require 'uri'

module RedmineAssistantHelper
  ALLOWED_DOCUMENTATION_DIRECTORIES = %w[
    analysis
    data
  ].freeze

  def redmine_assistant_score(score)
    format(
      '%.1f%%',
      score.to_f * 100
    )
  end

  def redmine_assistant_heading_path(heading_path)
    ERB::Util.html_escape(
      CGI.unescapeHTML(heading_path.to_s)
    ).html_safe
  end

  def redmine_assistant_content(content)
    markdown = rewrite_documentation_links(
      content.to_s
    )

    textilizable(
      markdown,
      object: @project
    )
  end

  def redmine_assistant_source_url(source_path)
    normalized_path = normalized_documentation_path(
      source_path
    )

    return nil if normalized_path.nil?

    base_url = configured_documentation_base_url

    return nil if base_url.nil?

    web_path = documentation_web_path(
      normalized_path
    )

    if web_path.empty?
      "#{base_url}/"
    else
      "#{base_url}/#{web_path}"
    end
  end

  private

  def configured_documentation_base_url
    base_url = ENV.fetch(
      'REDMINE_ASSISTANT_DOCS_BASE_URL',
      ''
    ).to_s.strip

    return nil if base_url.empty?

    uri = URI.parse(base_url)

    unless uri.is_a?(URI::HTTP) &&
           uri.host.present?
      Rails.logger.error(
        'REDMINE_ASSISTANT_DOCS_BASE_URL is invalid'
      )

      return nil
    end

    base_url.delete_suffix('/')
  rescue URI::InvalidURIError
    Rails.logger.error(
      'REDMINE_ASSISTANT_DOCS_BASE_URL is invalid'
    )

    nil
  end

  def documentation_web_path(source_path)
    return '' if source_path == 'index.md'

    if source_path.end_with?('.md')
      "#{source_path.delete_suffix('.md')}/"
    else
      "#{source_path}/"
    end
  end

  def rewrite_documentation_links(markdown)
    markdown.gsub(
      /\[([^\]]+)\]\(([^)]+)\)/
    ) do
      label = Regexp.last_match(1)
      destination = Regexp.last_match(2).to_s.strip

      rewritten_destination = safe_markdown_destination(
        destination
      )

      if rewritten_destination.nil?
        label
      else
        markdown_link(
          label,
          rewritten_destination
        )
      end
    end
  end

  def markdown_link(label, destination)
    format(
      '%c%s%c%c%s%c',
      91,
      label,
      93,
      40,
      destination,
      41
    )
  end

  def safe_markdown_destination(destination)
    uri = URI.parse(destination)

    if uri.is_a?(URI::HTTP)
      return destination
    end

    return nil if uri.scheme.present?
    return nil if destination.start_with?(
      '/',
      '//'
    )

    normalized_path = normalized_documentation_path(
      uri.path.to_s
    )

    return nil if normalized_path.nil?

    destination_url = redmine_assistant_source_url(
      normalized_path
    )

    return nil if destination_url.nil?

    if uri.fragment.present?
      "#{destination_url}##{uri.fragment}"
    else
      destination_url
    end
  rescue URI::InvalidURIError
    nil
  end

  def normalized_documentation_path(source_path)
    path = source_path.to_s.strip

    return nil if path.empty?
    return nil if path.include?("\0")
    return nil if path.start_with?('/')
    return nil if path.start_with?('internal_only/')

    components = path.split('/')

    return nil if components.include?('..')
    return 'index.md' if path == 'index.md'

    return nil unless components.length >= 2
    return nil unless ALLOWED_DOCUMENTATION_DIRECTORIES.include?(
      components.first
    )

    path
  end
end
