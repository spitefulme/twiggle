from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("trees", "0001_initial")]
    operations = [
        migrations.AddField(
            model_name="treetestrevision",
            name="method_format",
            field=models.CharField(default="tree-test", editable=False, max_length=12),
        ),
    ]
