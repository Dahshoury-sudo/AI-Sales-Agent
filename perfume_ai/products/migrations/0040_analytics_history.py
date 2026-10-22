from django.db import migrations, models
import django.db.models.deletion


def snapshot_items(apps, schema_editor):
    Item = apps.get_model("products", "OrderItem")
    alias = schema_editor.connection.alias
    batch = []
    for item in Item.objects.using(alias).select_related("variant__product", "order").only(
        "id", "variant_id", "order_id", "variant__volume", "variant__product_id",
        "variant__product__name", "variant__product__store_id", "order__store_id",
    ).iterator(chunk_size=1000):
        if item.variant_id and item.variant.product.store_id == item.order.store_id:
            item.product_snapshot_id = item.variant.product_id
            item.product_snapshot_name = item.variant.product.name
            item.volume_snapshot = item.variant.volume
            batch.append(item)
        if len(batch) == 1000:
            Item.objects.using(alias).bulk_update(batch, ["product_snapshot_id", "product_snapshot_name", "volume_snapshot"])
            batch = []
    if batch:
        Item.objects.using(alias).bulk_update(batch, ["product_snapshot_id", "product_snapshot_name", "volume_snapshot"])


class Migration(migrations.Migration):
    dependencies = [("products", "0039_handoff_attention")]
    operations = [
        migrations.AddField("orderitem", "product_snapshot_id", models.PositiveBigIntegerField(null=True, editable=False)),
        migrations.AddField("orderitem", "product_snapshot_name", models.CharField(max_length=200, blank=True, editable=False)),
        migrations.AddField("orderitem", "volume_snapshot", models.PositiveIntegerField(null=True, editable=False)),
        migrations.RunPython(snapshot_items, migrations.RunPython.noop),
        migrations.AlterField("orderitem", "variant", models.ForeignKey(to="products.productvariant", on_delete=django.db.models.deletion.SET_NULL, null=True, blank=True)),
        migrations.AddIndex("order", models.Index(fields=["store", "created_at"], name="order_analytics_date_idx")),
        migrations.AddIndex("conversation", models.Index(fields=["store", "created_at"], name="conv_analytics_date_idx")),
    ]
