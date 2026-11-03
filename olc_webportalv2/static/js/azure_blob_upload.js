(function(window) {
  'use strict';

  var DEFAULT_BLOCK_SIZE = 4 * 1024 * 1024;
  var DEFAULT_BLOCK_CONCURRENCY = 4;
  var DEFAULT_FILE_CONCURRENCY = 2;
  var DEFAULT_MAX_ATTEMPTS = 5;
  var DEFAULT_FILE_ATTEMPTS = 2;

  function retryDelay(attempt) {
    var backoff = Math.min(30000, 1000 * Math.pow(2, attempt - 2));
    return backoff + Math.floor(Math.random() * 1000);
  }

  function appendQueryParameters(url, parameters) {
    return url + (url.indexOf('?') === -1 ? '?' : '&') + parameters;
  }

  function encodeBlockId(index) {
    var value = String(index);
    while (value.length < 8) {
      value = '0' + value;
    }
    return window.btoa(value);
  }

  function escapeXml(value) {
    return value
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&apos;');
  }

  function isRetryable(status) {
    return status === 0 || status === 408 || status === 429 || status >= 500;
  }

  function isRetryableFileError(error) {
    return error.retryable || (
      error.status === 403 &&
      (error.phase === 'block' || error.phase === 'commit')
    );
  }

  function sendRequest(options) {
    return new Promise(function(resolve, reject) {
      var request = new XMLHttpRequest();
      request.open(options.method, options.url);
      request.timeout = 300000;

      Object.keys(options.headers || {}).forEach(function(headerName) {
        request.setRequestHeader(headerName, options.headers[headerName]);
      });

      if (options.onProgress && request.upload) {
        request.upload.onprogress = options.onProgress;
      }

      request.onload = function() {
        if (request.status >= 200 && request.status < 300) {
          resolve(request);
        } else {
          reject({
            status: request.status,
            responseText: request.responseText || '',
            retryable: isRetryable(request.status),
            phase: options.phase
          });
        }
      };

      request.onerror = function() {
        reject({
          status: request.status || 0,
          responseText: request.responseText || '',
          retryable: true,
          phase: options.phase
        });
      };

      request.ontimeout = function() {
        reject({
          status: request.status || 0,
          responseText: 'The upload request timed out.',
          retryable: true,
          phase: options.phase
        });
      };

      request.onabort = function() {
        reject({
          status: request.status || 0,
          responseText: 'The upload request was aborted.',
          retryable: false,
          phase: options.phase
        });
      };

      request.send(options.body === undefined ? null : options.body);
    });
  }

  function sendRequestWithRetry(options, maxAttempts) {
    var attempt = 1;

    function runAttempt() {
      return sendRequest(options).catch(function(error) {
        if (!error.retryable || attempt >= maxAttempts) {
          throw error;
        }
        attempt += 1;
        return new Promise(function(resolve) {
          window.setTimeout(resolve, retryDelay(attempt));
        }).then(runAttempt);
      });
    }

    return runAttempt();
  }

  function prepareUpload(prepareUrl, file, options) {
    var query = '?filename=' + encodeURIComponent(file.name) +
      '&size_bytes=' + encodeURIComponent(file.size);
    if (options.prefix) {
      query += '&prefix=' + encodeURIComponent(options.prefix);
    }

    return sendRequestWithRetry({
      method: 'GET',
      url: prepareUrl + query,
      phase: 'prepare'
    }, options.maxAttempts).then(function(request) {
      try {
        return JSON.parse(request.responseText);
      } catch (error) {
        throw {
          status: request.status,
          responseText: 'FoodPort returned invalid upload metadata.',
          retryable: false,
          phase: 'prepare'
        };
      }
    });
  }

  function uploadBlock(uploadUrl, chunk, blockId, onProgress, maxAttempts) {
    var blockUrl = appendQueryParameters(
      uploadUrl,
      'comp=block&blockid=' + encodeURIComponent(blockId)
    );
    return sendRequestWithRetry({
      method: 'PUT',
      url: blockUrl,
      headers: {'Content-Type': 'application/octet-stream'},
      body: chunk,
      onProgress: onProgress,
      phase: 'block'
    }, maxAttempts);
  }

  function commitBlockList(uploadUrl, blockIds, maxAttempts) {
    var blockListXml = '<?xml version="1.0" encoding="utf-8"?>' +
      '<BlockList>' +
      blockIds.map(function(blockId) {
        return '<Latest>' + escapeXml(blockId) + '</Latest>';
      }).join('') +
      '</BlockList>';

    return sendRequestWithRetry({
      method: 'PUT',
      url: appendQueryParameters(uploadUrl, 'comp=blocklist'),
      headers: {
        'Content-Type': 'application/xml; charset=UTF-8'
      },
      body: blockListXml,
      phase: 'commit'
    }, maxAttempts);
  }

  function uploadPreparedFile(file, uploadUrl, options) {
    var blockSize = options.blockSize || DEFAULT_BLOCK_SIZE;
    var blockConcurrency = options.blockConcurrency || DEFAULT_BLOCK_CONCURRENCY;
    var maxAttempts = options.maxAttempts || DEFAULT_MAX_ATTEMPTS;
    var blockCount = Math.ceil(file.size / blockSize);
    var blockIds = new Array(blockCount);
    var progressByBlock = new Array(blockCount);
    var nextBlockIndex = 0;
    var uploadedBytes = 0;
    var failure = null;

    if (blockCount > 50000) {
      return Promise.reject({
        status: 0,
        responseText: 'This file requires more than Azure Blob Storage supports.',
        retryable: false,
        phase: 'block'
      });
    }

    function uploadNextBlock() {
      if (failure || nextBlockIndex >= blockCount) {
        return Promise.resolve();
      }

      var index = nextBlockIndex;
      nextBlockIndex += 1;
      var start = index * blockSize;
      var chunk = file.slice(start, Math.min(start + blockSize, file.size));
      var blockId = encodeBlockId(index);
      blockIds[index] = blockId;
      progressByBlock[index] = 0;

      return uploadBlock(
        uploadUrl,
        chunk,
        blockId,
        function(event) {
          if (!event.lengthComputable) {
            return;
          }
          uploadedBytes += event.loaded - progressByBlock[index];
          progressByBlock[index] = event.loaded;
          if (options.onProgress) {
            options.onProgress(uploadedBytes, file.size);
          }
        },
        maxAttempts
      ).then(function() {
        uploadedBytes += chunk.size - progressByBlock[index];
        progressByBlock[index] = chunk.size;
        if (options.onProgress) {
          options.onProgress(uploadedBytes, file.size);
        }
        return uploadNextBlock();
      }).catch(function(error) {
        if (!failure) {
          failure = error;
        }
      });
    }

    var workers = [];
    var workerCount = Math.min(blockConcurrency, blockCount);
    for (var workerIndex = 0; workerIndex < workerCount; workerIndex += 1) {
      workers.push(uploadNextBlock());
    }

    return Promise.all(workers).then(function() {
      if (failure) {
        throw failure;
      }
      return commitBlockList(uploadUrl, blockIds, maxAttempts);
    });
  }

  function prepareAndUpload(file, prepareUrl, options) {
    options = options || {};
    options.maxAttempts = options.maxAttempts || DEFAULT_MAX_ATTEMPTS;
    var fileAttempts = options.fileAttempts || DEFAULT_FILE_ATTEMPTS;
    var attempt = 1;

    function runAttempt() {
      return prepareUpload(prepareUrl, file, options).then(function(details) {
        return uploadPreparedFile(file, details.upload_url, options).then(function() {
          return details;
        });
      }).catch(function(error) {
        if (!isRetryableFileError(error) || attempt >= fileAttempts) {
          throw error;
        }
        attempt += 1;
        if (options.onRetry) {
          options.onRetry(file, attempt, error);
        }
        if (options.onProgress) {
          options.onProgress(0, file.size);
        }
        return new Promise(function(resolve) {
          window.setTimeout(resolve, retryDelay(attempt));
        }).then(runAttempt);
      });
    }

    return runAttempt();
  }

  window.FoodPortAzureBlockUploader = {
    prepareAndUpload: prepareAndUpload,
    uploadFiles: function(files, prepareUrl, options) {
      options = options || {};
      var nextFileIndex = 0;
      var failure = null;
      var workerCount = Math.min(
        options.fileConcurrency || DEFAULT_FILE_CONCURRENCY,
        files.length
      );

      function uploadNextFile() {
        if (failure || nextFileIndex >= files.length) {
          return Promise.resolve();
        }

        var file = files[nextFileIndex];
        nextFileIndex += 1;
        if (options.onFileStart) {
          options.onFileStart(file);
        }
        var fileOptions = {};
        Object.keys(options).forEach(function(key) {
          fileOptions[key] = options[key];
        });
        fileOptions.onProgress = function(uploadedBytes, totalBytes) {
          if (options.onProgress) {
            options.onProgress(file, uploadedBytes, totalBytes);
          }
        };

        return prepareAndUpload(file, prepareUrl, fileOptions).then(function() {
          if (options.onFileComplete) {
            options.onFileComplete(file);
          }
          return uploadNextFile();
        }).catch(function(error) {
          error.fileName = file.name;
          if (options.onFileError) {
            options.onFileError(file, error);
          }
          if (!failure) {
            failure = error;
          }
        });
      }

      var workers = [];
      for (var workerIndex = 0; workerIndex < workerCount; workerIndex += 1) {
        workers.push(uploadNextFile());
      }

      return Promise.all(workers).then(function() {
        if (failure) {
          throw failure;
        }
      });
    }
  };
})(window);
