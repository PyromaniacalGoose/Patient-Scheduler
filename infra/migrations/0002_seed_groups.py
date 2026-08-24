from django.db import migrations

def create_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name="Viewer")
    Group.objects.get_or_create(name="Scheduler")

def remove_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name__in=["Viewer", "Scheduler"]).delete()

class Migration(migrations.Migration):
    dependencies = [("infra", "0001_initial")]
    operations = [
        migrations.RunPython(create_groups, reverse_code=remove_groups),
    ]