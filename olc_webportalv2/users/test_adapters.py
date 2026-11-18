from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

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


class MicrosoftUserProvisioningTests(TestCase):
    @override_settings(
        MICROSOFT={"valid_email_domains": ["inspection.gc.ca"]},
        MICROSOFT_CREATE_NEW_DJANGO_USER=True,
        MICROSOFT_NEW_DJANGO_USER_IS_STAFF=False,
    )
    def test_new_microsoft_users_do_not_receive_staff_access(self):
        from microsoft_authentication.auth.auth_utils import get_django_user

        user = get_django_user("new.user@inspection.gc.ca")

        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    @override_settings(
        MICROSOFT={"valid_email_domains": ["inspection.gc.ca"]},
        MICROSOFT_CREATE_NEW_DJANGO_USER=True,
        MICROSOFT_NEW_DJANGO_USER_IS_STAFF=False,
    )
    def test_microsoft_email_reuses_existing_local_account(self):
        from microsoft_authentication.auth.auth_utils import get_django_user

        user_model = get_user_model()
        existing_user = user_model.objects.create_user(
            username="existing.user",
            email="existing.user@inspection.gc.ca",
            password="test-password",
        )

        matched_user = get_django_user("existing.user@inspection.gc.ca")

        self.assertEqual(matched_user.pk, existing_user.pk)
