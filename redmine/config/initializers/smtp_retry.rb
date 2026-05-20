require 'mail'
require 'net/smtp'

module RedmineSmtpRetry
  MAX_RETRIES = 5
  RETRY_SLEEP_SECONDS = 5

  def deliver!(mail)
    retries = 0

    begin
      super
    rescue Net::SMTPFatalError => e
      if retries < MAX_RETRIES && e.message.include?('Access denied')
        retries += 1
        Rails.logger.warn "SMTP 554 Access denied, retry ##{retries}/#{MAX_RETRIES}: #{e.message}"
        sleep(RETRY_SLEEP_SECONDS)
        retry
      end
      raise
    rescue Net::SMTPServerDisconnected => e
      if retries < MAX_RETRIES && e.message.include?('wrong version number')
        retries += 1
        Rails.logger.warn "SMTP disconnected, retry ##{retries}/#{MAX_RETRIES}: #{e.message}"
        sleep(RETRY_SLEEP_SECONDS)
        retry
      end
      raise
    end
  end
end

Mail::SMTP.prepend(RedmineSmtpRetry)
