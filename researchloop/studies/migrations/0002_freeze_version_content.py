from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("studies", "0001_initial")]
    operations = [
        migrations.RunSQL(
            """
        CREATE FUNCTION u1_frozen_version_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.content IS DISTINCT FROM OLD.content OR
             NEW.frozen_revision IS DISTINCT FROM OLD.frozen_revision OR
             NEW.study_id IS DISTINCT FROM OLD.study_id OR
             NEW.number IS DISTINCT FROM OLD.number OR
             NEW.published_at IS DISTINCT FROM OLD.published_at THEN
            RAISE EXCEPTION 'Published definitions are immutable';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER u1_frozen_version BEFORE UPDATE ON studies_publishedversion
        FOR EACH ROW EXECUTE FUNCTION u1_frozen_version_guard();
        """,
            "DROP TRIGGER u1_frozen_version ON studies_publishedversion; DROP FUNCTION u1_frozen_version_guard();",
        )
    ]
