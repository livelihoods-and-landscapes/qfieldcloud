# Generated by Django 3.2.8 on 2021-10-18 09:52

import datetime

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0047_useraccount_timezone"),
    ]

    operations = [
        migrations.AddField(
            model_name="useraccount",
            name="notifs_frequency",
            field=models.DurationField(
                blank=True,
                choices=[
                    (datetime.timedelta(0), "Immediately"),
                    (datetime.timedelta(seconds=3600), "Hourly"),
                    (datetime.timedelta(days=1), "Daily"),
                    (datetime.timedelta(days=7), "Weekly"),
                    (None, "Disabled"),
                ],
                default=None,
                null=True,
                verbose_name="Email frequency for notifications",
            ),
        ),
    ]
