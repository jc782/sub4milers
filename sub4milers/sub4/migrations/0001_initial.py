# Generated by Django 3.2.6 on 2023-07-23 18:49

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='AthleteSub4',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=1000)),
                ('firstTime', models.TimeField()),
                ('firstDate', models.DateField(blank=True, null=True)),
                ('firstIndoor', models.BinaryField(blank=True, null=True)),
                ('pbTime', models.TimeField()),
                ('pbDate', models.DateField(blank=True, null=True)),
                ('pbIndoor', models.BinaryField(blank=True, null=True)),
                ('countries', models.CharField(max_length=100)),
            ],
        ),
    ]
