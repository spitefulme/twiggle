import uuid
from django.db import migrations, models


def move_to_owners(apps, schema_editor):
    Draft = apps.get_model("studies", "StudyDraft")
    Version = apps.get_model("studies", "PublishedVersion")
    Card = apps.get_model("cards", "CardSortRevision")
    Tree = apps.get_model("trees", "TreeTestRevision")
    for draft in Draft.objects.select_related("study").all():
        owner = Card if draft.study.method == "card-sort" else Tree
        row = owner.objects.create(
            definition_id=draft.pk,
            revision=draft.revision,
            frozen=False,
            content=draft.content,
        )
        draft.method_revision = row.pk
        draft.save(update_fields=["method_revision"])
    for version in Version.objects.select_related("study").all():
        owner = Card if version.study.method == "card-sort" else Tree
        draft = Draft.objects.get(study=version.study)
        owner.objects.create(
            id=version.frozen_revision,
            definition_id=draft.pk,
            revision=version.number,
            frozen=True,
            content=version.content,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("studies", "0002_freeze_version_content"),
        ("cards", "0001_initial"),
        ("trees", "0001_initial"),
    ]
    operations = [
        migrations.RunSQL(
            "DROP TRIGGER u1_frozen_version ON studies_publishedversion; DROP FUNCTION u1_frozen_version_guard();"
        ),
        migrations.AddField(
            model_name="studydraft",
            name="method_revision",
            field=models.UUIDField(default=uuid.uuid4),
        ),
        migrations.RunPython(move_to_owners),
        migrations.RemoveField(model_name="studydraft", name="content"),
        migrations.RemoveField(model_name="publishedversion", name="content"),
        migrations.RunSQL("""
        CREATE FUNCTION u1_frozen_version_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.frozen_revision IS DISTINCT FROM OLD.frozen_revision OR
             NEW.study_id IS DISTINCT FROM OLD.study_id OR
             NEW.number IS DISTINCT FROM OLD.number OR
             NEW.published_at IS DISTINCT FROM OLD.published_at THEN
            RAISE EXCEPTION 'Published references are immutable';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER u1_frozen_version BEFORE UPDATE ON studies_publishedversion
        FOR EACH ROW EXECUTE FUNCTION u1_frozen_version_guard();
        CREATE FUNCTION u1_method_revision_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Method revision snapshots are immutable'; END $$;
        CREATE TRIGGER u1_card_revision BEFORE UPDATE ON cards_cardsortrevision
        FOR EACH ROW EXECUTE FUNCTION u1_method_revision_guard();
        CREATE TRIGGER u1_tree_revision BEFORE UPDATE ON trees_treetestrevision
        FOR EACH ROW EXECUTE FUNCTION u1_method_revision_guard();
        """),
    ]
