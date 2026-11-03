(function () {
  'use strict';

  function csrfToken() {
    var element = document.querySelector('meta[name="csrf-token"]');
    return element ? element.getAttribute('content') : '';
  }

  function setDisabled(container, disabled) {
    container.querySelectorAll('button, select, textarea').forEach(function (element) {
      element.disabled = disabled;
    });
  }

  function submitFeedback(container, rating, reason, comment) {
    var status = container.querySelector('.redmine-assistant-feedback-status');
    status.classList.remove('error');
    status.textContent = '';
    setDisabled(container, true);

    var body = new URLSearchParams();
    body.append('request_id', container.dataset.requestId);
    body.append('rating', rating);
    if (reason) body.append('reason', reason);
    if (comment) body.append('comment', comment);

    fetch(container.dataset.feedbackUrl, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
        'X-CSRF-Token': csrfToken(),
        'X-Requested-With': 'XMLHttpRequest'
      },
      body: body.toString()
    }).then(function (response) {
      return response.json().then(function (payload) {
        if (!response.ok) throw new Error(payload.message || 'Feedback failed');
        return payload;
      });
    }).then(function (payload) {
      status.textContent = payload.message;
      container.querySelector('.redmine-assistant-feedback-actions').hidden = true;
      container.querySelector('.redmine-assistant-feedback-details').hidden = true;
    }).catch(function (error) {
      status.classList.add('error');
      status.textContent = error.message;
      setDisabled(container, false);
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.redmine-assistant-feedback').forEach(function (container) {
      var details = container.querySelector('.redmine-assistant-feedback-details');
      var reason = container.querySelector('#redmine-assistant-feedback-reason');
      var comment = container.querySelector('#redmine-assistant-feedback-comment');

      container.querySelector('.redmine-assistant-feedback-helpful').addEventListener('click', function () {
        submitFeedback(container, 'helpful', null, null);
      });

      container.querySelector('.redmine-assistant-feedback-unhelpful').addEventListener('click', function () {
        details.hidden = false;
        reason.focus();
      });

      container.querySelector('.redmine-assistant-feedback-submit').addEventListener('click', function () {
        if (!reason.value) {
          reason.focus();
          return;
        }
        submitFeedback(container, 'unhelpful', reason.value, comment.value.trim());
      });
    });
  });
}());
