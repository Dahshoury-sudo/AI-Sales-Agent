from io import BytesIO

import openpyxl
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from products.models import Brand, Category, Product, ProductVariant, Store
from products.occasions import DAILY, OCCASIONS
from products.scent_tags import normalize_scent_tags
from products.services.bulk_import import parse_excel
from products.services.scent_tag_import import export_scent_tags, import_scent_tags


def workbook_bytes(headers, rows):
    workbook = openpyxl.Workbook()
    workbook.active.append(headers)
    for row in rows:
        workbook.active.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


class ScentLabelCatalogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="labels-owner")
        cls.store = Store.objects.create(name="Label shop", owner=cls.user)
        cls.other = Store.objects.create(name="Other shop")
        cls.brand = Brand.objects.create(name="House", store=cls.store)
        cls.category = Category.objects.create(name="Perfume", store=cls.store)
        cls.product = Product.objects.create(store=cls.store, brand=cls.brand, name="First",
                                              gender="unisex", category=cls.category, scent_tags=["fresh"])
        cls.second = Product.objects.create(store=cls.store, brand=cls.brand, name="Second", gender="unisex")
        cls.foreign = Product.objects.create(store=cls.other, brand=cls.brand, name="Foreign", gender="unisex")

    def setUp(self):
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + str(RefreshToken.for_user(self.user).access_token))

    def test_normalization_and_invalid_arrays(self):
        self.assertEqual(normalize_scent_tags([" Fresh ", "woody", "fresh"]), ["fresh", "woody"])
        for value in ("fresh", None, ["frseh"], [1], {"fresh": True}):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                normalize_scent_tags(value)

    def test_product_defaults_and_admin_validation(self):
        self.assertEqual(self.second.scent_tags, [])
        self.product.scent_tags = [" SWEET ", "sweet"]
        self.product.full_clean()
        self.assertEqual(self.product.scent_tags, ["sweet"])
        self.product.scent_tags = ["typo"]
        with self.assertRaises(ValidationError):
            self.product.full_clean()

    def test_product_apis_round_trip_and_clear_tags(self):
        url = f"/dashboard/api/products/{self.product.pk}/"
        response = self.client.put(url, {"scent_tags": ["Sweet", "woody"]}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(url).json()["scent_tags"], ["sweet", "woody"])
        rows = self.client.get("/dashboard/api/products/").json()["products"]
        self.assertEqual(next(row for row in rows if row["id"] == self.product.pk)["scent_tags"], ["sweet", "woody"])
        self.assertEqual(self.client.put(url, {"scent_tags": []}, format="json").status_code, 200)
        self.product.refresh_from_db()
        self.assertEqual(self.product.scent_tags, [])

    def test_invalid_tags_do_not_partially_edit_product(self):
        response = self.client.put(f"/dashboard/api/products/{self.product.pk}/",
                                   {"name": "Changed", "scent_tags": ["typo"]}, format="json")
        self.assertEqual(response.status_code, 400)
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "First")
        self.assertEqual(self.product.scent_tags, ["fresh"])

    def test_create_api_accepts_tags_and_rejects_invalid_before_creating(self):
        payload = {"name": "New", "brand": "House", "scent_tags": ["Oud", "heavy"]}
        response = self.client.post("/dashboard/api/products/create/", payload, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Product.objects.get(pk=response.json()["id"]).scent_tags, ["oud", "heavy"])
        payload.update(name="Invalid", scent_tags=["oops"])
        self.assertEqual(self.client.post("/dashboard/api/products/create/", payload, format="json").status_code, 400)
        self.assertFalse(Product.objects.filter(name="Invalid").exists())

    def test_export_is_store_scoped_and_can_be_imported(self):
        response = self.client.get("/api/import/scent-tags/")
        self.assertEqual(response.status_code, 200)
        workbook = openpyxl.load_workbook(BytesIO(response.content))
        rows = list(workbook.active.values)
        self.assertEqual(rows[0], ("product_id", "name", "brand", "scent_tags"))
        self.assertEqual({row[0] for row in rows[1:]}, {self.product.pk, self.second.pk})
        self.assertEqual(len(list(workbook["Allowed labels"].values)), 17)
        workbook.close()
        result = import_scent_tags(response.content, self.store)
        self.assertEqual((result["updated"], result["unchanged"], result["skipped"]), (0, 1, 1))

    def test_bulk_update_updates_existing_rows_only(self):
        data = workbook_bytes(["product_id", "name", "scent_tags"],
                              [[self.product.pk, "Ignored name", "Sweet, woody, sweet"],
                               [self.second.pk, "Ignored", "oud, heavy"]])
        response = self.client.post("/api/import/scent-tags/", {"file": SimpleUploadedFile("tags.xlsx", data)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["updated"], 2)
        self.product.refresh_from_db()
        self.assertEqual((self.product.name, self.product.scent_tags), ("First", ["sweet", "woody"]))
        self.assertEqual(Product.objects.count(), 3)

    def test_invalid_workbooks_are_atomic(self):
        for invalid_row in ([self.foreign.pk, "oud"], [999999, "oud"], [self.second.pk, "frseh"],
                            [self.product.pk, "oud"], ["bad id", "oud"]):
            with self.subTest(row=invalid_row):
                data = workbook_bytes(["product_id", "scent_tags"], [[self.product.pk, "sweet"], invalid_row])
                result = import_scent_tags(data, self.store)
                self.assertTrue(result["errors"])
                self.assertEqual(result["updated"], 0)
                self.product.refresh_from_db()
                self.assertEqual(self.product.scent_tags, ["fresh"])

    def test_blank_cells_preserve_existing_tags(self):
        data = workbook_bytes(["product_id", "scent_tags"], [[self.product.pk, None]])
        self.assertEqual(import_scent_tags(data, self.store)["skipped"], 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.scent_tags, ["fresh"])

    def test_export_can_be_uploaded_with_the_vocabulary_sheet_open(self):
        workbook = openpyxl.load_workbook(BytesIO(export_scent_tags(self.store)))
        workbook["Scent labels"]["D2"] = "sweet, woody"
        workbook.active = 1
        buffer = BytesIO()
        workbook.save(buffer)
        workbook.close()
        self.assertEqual(import_scent_tags(buffer.getvalue(), self.store)["updated"], 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.scent_tags, ["sweet", "woody"])

    def test_duplicate_required_headers_are_rejected_without_changes(self):
        data = workbook_bytes(["product_id", "scent_tags", "scent_tags"], [[self.product.pk, "fresh", "sweet"]])
        result = import_scent_tags(data, self.store)
        self.assertTrue(result["errors"])
        self.product.refresh_from_db()
        self.assertEqual(self.product.scent_tags, ["fresh"])

    def test_import_requires_authentication(self):
        client = APIClient()
        self.assertEqual(client.get("/api/import/scent-tags/").status_code, 401)
        self.assertEqual(client.post("/api/import/scent-tags/", {}).status_code, 401)

    def test_old_and_new_product_workbooks(self):
        headers = ["name", "brand", "norm_vol_1", "norm_price_1"]
        self.assertEqual(parse_excel(workbook_bytes(headers, [["Legacy", "House", 50, 500]]), self.store)["created"], 1)
        self.assertEqual(Product.objects.get(name="Legacy").scent_tags, [])
        headers.append("scent_tags")
        result = parse_excel(workbook_bytes(headers, [["Tagged", "House", 50, 500, "fresh, CLEAN"]]), self.store)
        self.assertEqual(result["created"], 1)
        self.assertEqual(Product.objects.get(name="Tagged").scent_tags, ["fresh", "clean"])
        result = parse_excel(workbook_bytes(headers, [["Bad", "House", 50, 500, "typo"]]), self.store)
        self.assertEqual(result["skipped"], 1)
        self.assertFalse(Product.objects.filter(name="Bad").exists())

    def test_template_example_imports_correct_variants_and_labels(self):
        response = self.client.get("/api/import/template/")
        workbook = openpyxl.load_workbook(BytesIO(response.content))
        headers = next(workbook.active.values)
        self.assertEqual(headers.index("scent_tags"), headers.index("occasion") + 1)
        workbook.close()
        result = parse_excel(response.content, self.store)
        self.assertEqual(result["created"], 1, result)
        product = Product.objects.get(name="Bleu de Chanel")
        self.assertEqual(product.scent_tags, ["fresh", "woody"])
        self.assertEqual((product.occasion, product.longevity, product.projection),
                         (DAILY, "8 hours", "Moderate"))
        self.assertEqual(list(product.variants.values_list("volume", "bottle_type")),
                         [(50, "normal"), (90, "normal"), (100, "original"), (200, "original")])

    def test_dashboard_renders_all_selectable_labels(self):
        response = self.client.get("/dashboard/")
        self.assertEqual(response.content.decode().count('<input type="checkbox" name="edit-scent-tag"'), 16)
        self.assertContains(response, 'value="heavy"')

    def test_six_occasion_labels_import_with_adjacent_scent_tags(self):
        headers = ["name", "brand", "occasion", "scent_tags", "longevity", "norm_vol_1", "norm_price_1"]
        rows = [[f"Occasion {index}", "House", occasion, "fresh, clean", "8 hours", 50, 500]
                for index, occasion in enumerate(OCCASIONS)]
        result = parse_excel(workbook_bytes(headers, rows), self.store)
        self.assertEqual(result["created"], 6, result)
        for index, occasion in enumerate(OCCASIONS):
            product = Product.objects.get(name=f"Occasion {index}")
            self.assertEqual((product.occasion, product.scent_tags, product.longevity),
                             (occasion, ["fresh", "clean"], "8 hours"))
