# Generated by Django 4.1 on 2023-12-17 15:30

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sub4', '0006_athletesub4_bestdate'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='athletesub4',
            name='bestDate',
        ),
        migrations.RemoveField(
            model_name='athletesub4',
            name='bestTime',
        ),
        migrations.AlterField(
            model_name='athletesub4',
            name='IndoorTime',
            field=models.TimeField(blank=True, null=True),
        ),
    ]