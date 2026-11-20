(function () {
  const dropzone = window.Dropzone;

  if (!dropzone || typeof window.gettext !== 'function') {
    throw new Error('Dropzone localization requires Dropzone and the Django JavaScript catalog.');
  }

  Object.assign(dropzone.prototype.defaultOptions, {
    dictDefaultMessage: window.gettext('Drop files to upload or click'),
    dictFallbackMessage: window.gettext("Your browser does not support drag'n'drop file uploads."),
    dictFallbackText: window.gettext('Please use the fallback form below to upload your files like in the olden days.'),
    dictFallbackButton: window.gettext('Upload!'),
    dictFileTooBig: window.gettext('File is too big ({{filesize}}MiB). Max filesize: {{maxFilesize}}MiB.'),
    dictInvalidFileType: window.gettext("You can't upload files of this type."),
    dictResponseError: window.gettext('Server responded with {{statusCode}} code.'),
    dictCancelUpload: window.gettext('Cancel upload'),
    dictUploadCanceled: window.gettext('Upload canceled.'),
    dictCancelUploadConfirmation: window.gettext('Are you sure you want to cancel this upload?'),
    dictMaxFilesExceeded: window.gettext('You can not upload any more files.'),
    dictFileSizeUnits: {
      tb: window.gettext('TB'),
      gb: window.gettext('GB'),
      mb: window.gettext('MB'),
      kb: window.gettext('KB'),
      b: window.gettext('B'),
    },
  });
})();
