# Generated by Django 4.1 on 2023-12-17 15:33

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sub4', '0007_remove_athletesub4_bestdate_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='athletesub4',
            name='bestDate',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='athletesub4',
            name='bestTime',
            field=models.TimeField(blank=True, null=True),
        ),
    ]
