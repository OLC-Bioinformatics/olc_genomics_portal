from django.test import SimpleTestCase, override_settings

from olc_webportalv2.users.adapters import AccountAdapter, SocialAccountAdapter


class RegistrationAdapterTests(SimpleTestCase):
    @override_settings(ACCOUNT_ALLOW_REGISTRATION=True)
    def test_account_and_social_adapters_allow_registration(self):
        account_adapter = AccountAdapter()
        social_adapter = SocialAccountAdapter()

        self.assertTrue(account_adapter.is_open_for_signup(None))
        self.assertTrue(social_adapter.is_open_for_signup(None, None))

    @override_settings(ACCOUNT_ALLOW_REGISTRATION=False)
    def test_account_and_social_adapters_respect_registration_setting(self):
        account_adapter = AccountAdapter()
        social_adapter = SocialAccountAdapter()

        self.assertFalse(account_adapter.is_open_for_signup(None))
        self.assertFalse(social_adapter.is_open_for_signup(None, None))
