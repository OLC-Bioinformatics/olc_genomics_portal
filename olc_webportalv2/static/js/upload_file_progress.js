(function(window) {
  'use strict';

  function create(container, files, fileAttempts) {
    fileAttempts = fileAttempts || 2;
    container.textContent = '';
    var heading = document.createElement('h5');
    heading.textContent = 'File upload progress';
    container.appendChild(heading);

    var entries = files.map(function(file) {
      var row = document.createElement('div');
      row.className = 'mb-3';

      var heading = document.createElement('div');
      heading.className = 'd-flex justify-content-between';

      var fileName = document.createElement('span');
      fileName.className = 'text-break';
      fileName.textContent = file.name;

      var status = document.createElement('span');
      status.className = 'text-muted ml-2';
      status.textContent = 'Queued';

      heading.appendChild(fileName);
      heading.appendChild(status);

      var progress = document.createElement('div');
      progress.className = 'progress';
      progress.setAttribute('role', 'progressbar');
      progress.setAttribute('aria-label', file.name);
      progress.setAttribute('aria-valuemin', '0');
      progress.setAttribute('aria-valuemax', '100');
      progress.setAttribute('aria-valuenow', '0');

      var bar = document.createElement('div');
      bar.className = 'progress-bar progress-bar-striped progress-bar-animated';
      bar.style.width = '0%';
      bar.textContent = '0%';
      progress.appendChild(bar);

      row.appendChild(heading);
      row.appendChild(progress);
      container.appendChild(row);

      return {
        file: file,
        row: row,
        status: status,
        progress: progress,
        bar: bar,
        state: 'queued'
      };
    });

    function findEntry(file) {
      for (var index = 0; index < entries.length; index += 1) {
        if (entries[index].file === file) {
          return entries[index];
        }
      }
      return null;
    }

    function setPercent(entry, percent) {
      var boundedPercent = Math.max(0, Math.min(100, percent));
      entry.progress.setAttribute('aria-valuenow', String(boundedPercent));
      entry.bar.style.width = boundedPercent + '%';
      entry.bar.textContent = boundedPercent + '%';
    }

    function setFailure(entry, error) {
      entry.state = 'failed';
      entry.status.textContent = 'Failed' +
        (error.phase ? ' during ' + error.phase : '');
      entry.bar.className = 'progress-bar bg-danger';
    }

    entries.forEach(function(entry) {
      container.appendChild(entry.row);
    });

    return {
      onFileStart: function(file) {
        var entry = findEntry(file);
        if (entry && entry.state === 'queued') {
          entry.state = 'uploading';
          entry.status.textContent = 'Preparing upload';
        }
      },
      onProgress: function(file, uploadedBytes, totalBytes) {
        var entry = findEntry(file);
        if (!entry || entry.state === 'complete' || entry.state === 'failed') {
          return;
        }
        var percent = totalBytes ?
          Math.round(uploadedBytes / totalBytes * 100) : 0;
        setPercent(entry, percent);
        if (entry.state !== 'retrying' || uploadedBytes > 0) {
          entry.state = 'uploading';
          entry.status.textContent = 'Uploading (' + percent + '%)';
        }
      },
      onRetry: function(file, attempt) {
        var entry = findEntry(file);
        if (entry) {
          entry.state = 'retrying';
          entry.status.textContent = (
            'Retrying (attempt ' + attempt + ' of ' + fileAttempts + ')'
          );
          setPercent(entry, 0);
        }
      },
      onFileComplete: function(file) {
        var entry = findEntry(file);
        if (entry) {
          entry.state = 'complete';
          entry.status.textContent = 'Complete';
          entry.bar.className = 'progress-bar bg-success';
          setPercent(entry, 100);
        }
      },
      onFileError: function(file, error) {
        var entry = findEntry(file);
        if (entry) {
          setFailure(entry, error);
        }
      },
      onUploadFailed: function() {
        entries.forEach(function(entry) {
          if (entry.state === 'queued') {
            entry.state = 'skipped';
            entry.status.textContent = 'Not uploaded';
          }
        });
      }
    };
  }

  window.FoodPortUploadProgress = {
    create: create
  };
})(window);
