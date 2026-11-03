# frozen_string_literal: true

require_relative '../test_helper'

class RedmineAssistantHelperTest < ActiveSupport::TestCase
  include RedmineAssistantHelper

  def setup
    @project = Project.new(
      name: 'Assistant Test Project',
      identifier: 'assistant-test'
    )
  end

  def test_score_is_formatted_as_percentage
    assert_equal(
      '91.0%',
      redmine_assistant_score(0.91)
    )
  end

  def test_heading_entities_are_decoded
    heading = redmine_assistant_heading_path(
      'GeneSeekr &gt; Description'
    )

    assert_equal(
      'GeneSeekr &gt; Description',
      heading
    )

    assert_equal(
      'GeneSeekr > Description',
      CGI.unescapeHTML(heading)
    )
  end

  def test_source_url_is_nil_without_base_url
    ENV.stubs(:fetch)
       .with(
         'REDMINE_ASSISTANT_DOCS_BASE_URL',
         ''
       )
       .returns('')

    assert_nil(
      redmine_assistant_source_url(
        'analysis/mobsuite.md'
      )
    )
  end

  def test_standard_source_url_is_constructed
    ENV.stubs(:fetch)
       .with(
         'REDMINE_ASSISTANT_DOCS_BASE_URL',
         ''
       )
       .returns(
         'https://docs.example.gc.ca/redmine'
       )

    assert_equal(
      'https://docs.example.gc.ca/redmine/analysis/mobsuite/',
      redmine_assistant_source_url(
        'analysis/mobsuite.md'
      )
    )
  end

  def test_trailing_slash_is_removed_from_base_url
    ENV.stubs(:fetch)
       .with(
         'REDMINE_ASSISTANT_DOCS_BASE_URL',
         ''
       )
       .returns(
         'https://docs.example.gc.ca/redmine/'
       )

    assert_equal(
      'https://docs.example.gc.ca/redmine/',
      redmine_assistant_source_url(
        'index.md'
      )
    )
  end

  def test_internal_source_url_is_rejected
    ENV.stubs(:fetch)
       .with(
         'REDMINE_ASSISTANT_DOCS_BASE_URL',
         ''
       )
       .returns(
         'https://docs.example.gc.ca/redmine'
       )

    assert_nil(
      redmine_assistant_source_url(
        'internal_only/merge.md'
      )
    )
  end

  def test_parent_directory_path_is_rejected
    ENV.stubs(:fetch)
       .with(
         'REDMINE_ASSISTANT_DOCS_BASE_URL',
         ''
       )
       .returns(
         'https://docs.example.gc.ca/redmine'
       )

    assert_nil(
      redmine_assistant_source_url(
        '../internal_only/merge.md'
      )
    )
  end

  def test_absolute_path_is_rejected
    ENV.stubs(:fetch)
       .with(
         'REDMINE_ASSISTANT_DOCS_BASE_URL',
         ''
       )
       .returns(
         'https://docs.example.gc.ca/redmine'
       )

    assert_nil(
      redmine_assistant_source_url(
        '/etc/passwd'
      )
    )
  end

  def test_unapproved_directory_is_rejected
    ENV.stubs(:fetch)
       .with(
         'REDMINE_ASSISTANT_DOCS_BASE_URL',
         ''
       )
       .returns(
         'https://docs.example.gc.ca/redmine'
       )

    assert_nil(
      redmine_assistant_source_url(
        'private/example.md'
      )
    )
  end

  def test_invalid_base_url_is_rejected
    ENV.stubs(:fetch)
       .with(
         'REDMINE_ASSISTANT_DOCS_BASE_URL',
         ''
       )
       .returns('not a valid URL')

    assert_nil(
      redmine_assistant_source_url(
        'analysis/mobsuite.md'
      )
    )
  end
  def test_relative_documentation_link_is_rewritten
    ENV.stubs(:fetch)
      .with(
        'REDMINE_ASSISTANT_DOCS_BASE_URL',
        ''
      )
      .returns(
        'https://docs.example.gc.ca/redmine'
      )

    rewritten = send(
      :rewrite_documentation_links,
      'Use [MobSuite](analysis/mobsuite.md).'
    )

    assert_equal(
      'Use [MobSuite]' \
      '(https://docs.example.gc.ca/redmine/analysis/mobsuite/).',
      rewritten
    )
  end
  def test_documentation_link_fragment_is_preserved
    ENV.stubs(:fetch)
      .with(
        'REDMINE_ASSISTANT_DOCS_BASE_URL',
        ''
      )
      .returns(
        'https://docs.example.gc.ca/redmine'
      )

    rewritten = send(
      :rewrite_documentation_links,
      '[GeneSeekr]' \
      '(analysis/geneseekr.md#interpreting-results)'
    )

    assert_equal(
      '[GeneSeekr]' \
      '(https://docs.example.gc.ca/redmine/' \
      'analysis/geneseekr/#interpreting-results)',
      rewritten
    )
  end
  def test_bare_documentation_path_is_not_rewritten
    ENV.stubs(:fetch)
      .with(
        'REDMINE_ASSISTANT_DOCS_BASE_URL',
        ''
      )
      .returns(
        'https://docs.example.gc.ca/redmine'
      )

    rewritten = send(
      :rewrite_documentation_links,
      'Use analysis/mobsuite.md.'
    )

    assert_equal(
      'Use analysis/mobsuite.md.',
      rewritten
    )
  end
end
