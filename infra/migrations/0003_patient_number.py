from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("infra", "0002_seed_groups"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE SEQUENCE infra_patient_number_seq
                START WITH 100001
                INCREMENT BY 1;
            """,
            reverse_sql="""
                DROP SEQUENCE IF EXISTS infra_patient_number_seq;
            """,
        ),

        migrations.RunSQL(
            sql="""
                ALTER TABLE infra_patient
                ALTER COLUMN patient_number
                SET DEFAULT nextval('infra_patient_number_seq');
            """,
            reverse_sql="""
                ALTER TABLE infra_patient
                ALTER COLUMN patient_number
                DROP DEFAULT;
            """,
        ),
    ]