from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("governance", "0002_installationrecord")]
    operations = [
        migrations.RunSQL(
            """
        CREATE FUNCTION u1_provenance_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Installation provenance cannot be deleted';
          END IF;
          IF OLD.mode <> 'fresh-local' AND NEW.mode = 'fresh-local' THEN
            RAISE EXCEPTION 'Restored or unknown provenance cannot become fresh-local';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER u1_provenance BEFORE UPDATE OR DELETE ON governance_installationrecord
        FOR EACH ROW EXECUTE FUNCTION u1_provenance_guard();
        """,
            "DROP TRIGGER u1_provenance ON governance_installationrecord; DROP FUNCTION u1_provenance_guard();",
        )
    ]
