# Generated by Django 3.2.2 on 2021-05-17 09:36

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0036_auto_20210426_1210"),
    ]

    operations = [
        migrations.AlterField(
            model_name="project",
            name="owner",
            field=models.ForeignKey(
                help_text="The project owner can be either you or any of the organization you are member of.",
                limit_choices_to=models.Q(("user_type__in", [1, 2])),
                on_delete=django.db.models.deletion.CASCADE,
                related_name="projects",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]