from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('governance', '0005_u5_erasure_state')]
    operations = [migrations.AlterField(model_name='installationrecord', name='mode', field=models.CharField(max_length=20, choices=[('fresh-local', 'Fresh synthetic local'), ('restored', 'Restored'), ('unknown', 'Unknown'), ('portable-core', 'Portable core')]))]
