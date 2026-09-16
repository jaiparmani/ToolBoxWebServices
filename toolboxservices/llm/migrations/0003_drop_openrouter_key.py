"""Drop the key table.

The keys live on llm-gateway now. Leaving the table would leave a second place
to put a credential, which is the thing having a gateway is meant to prevent.

Reversible in schema but NOT in data: the rows are gone. Anything still in this
table when the migration runs must already be on the gateway.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("llm", "0002_alter_openrouterkey_options_and_more"),
    ]

    operations = [
        migrations.DeleteModel(name="OpenRouterKey"),
    ]
