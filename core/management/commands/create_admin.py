import os

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model


class Command(BaseCommand):
    help = "Create the production admin account from environment variables."

    def handle(self, *args, **options):
        User = get_user_model()

        username = os.environ.get("ADMIN_USERNAME")
        email = os.environ.get("ADMIN_EMAIL", "")
        password = os.environ.get("ADMIN_PASSWORD")

        if not username or not password:
            self.stdout.write(
                self.style.WARNING(
                    "ADMIN_USERNAME or ADMIN_PASSWORD is not set. "
                    "Skipping admin creation."
                )
            )
            return

        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "email": email,
                "is_staff": True,
                "is_superuser": True,
            },
        )

        if created:
            user.set_password(password)
            user.is_staff = True
            user.is_superuser = True
            user.email = email
            user.save()

            self.stdout.write(
                self.style.SUCCESS(
                    f"Admin account '{username}' created successfully."
                )
            )

        else:
            user.set_password(password)

            user.is_staff = True
            user.is_superuser = True

            if email:
                user.email = email

            user.save()

            self.stdout.write(
                self.style.SUCCESS(
                    f"Admin account '{username}' updated successfully."
                )
            )