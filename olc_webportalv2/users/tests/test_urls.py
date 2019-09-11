from django.urls import reverse, resolve

from test_plus.test import TestCase


class TestUserURLs(TestCase):
    """Test URL patterns for users app."""

    def setUp(self):
        self.user = self.make_user()

    def test_list_reverse(self):
        """users:list should reverse to /users/."""
        self.assertEqual(reverse('users:list'), '/en-ca/users/')

    def test_list_resolve(self):
        """/users/ should resolve to users:list."""
        self.assertEqual(resolve('/en-ca/users/').view_name, 'users:list')

    def test_redirect_reverse(self):
        """users:redirect should reverse to /users/~redirect/."""
        self.assertEqual(reverse('users:redirect'), '/en-ca/users/~redirect/')

    def test_redirect_resolve(self):
        """/users/~redirect/ should resolve to users:redirect."""
        self.assertEqual(
            resolve('/en-ca/users/~redirect/').view_name,
            'users:redirect'
        )

    def test_detail_reverse(self):
        """users:detail should reverse to /users/testuser/."""
        self.assertEqual(
            reverse('users:detail', kwargs={'username': 'testuser'}),
            '/en-ca/users/testuser/'
        )

    def test_detail_resolve(self):
        """/en-ca/users/testuser/ should resolve to users:detail."""
        self.assertEqual(resolve('/en-ca/users/testuser/').view_name, 'users:detail')

    def test_update_reverse(self):
        """users:update should reverse to /users/~update/."""
        self.assertEqual(reverse('users:update'), '/en-ca/users/~update/')

    def test_update_resolve(self):
        """/users/~update/ should resolve to users:update."""
        self.assertEqual(
            resolve('/en-ca/users/~update/').view_name,
            'users:update'
        )
