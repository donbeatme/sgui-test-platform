import ast
import io
import zipfile
from types import SimpleNamespace
from django.test import SimpleTestCase
from .contract import validate, runtime_required
from .guided import fragment_statements, wrap_fragment
from .export import bundle


class BrowserLanguageTests(SimpleTestCase):
    def issues(self, body):
        return validate(wrap_fragment('await page.goto(params["base_url"])\n' + body, 1),
                        {'steps': [{'step_number': 1}]}, {'base_url': 'https://example.org'}, require_assertions=False)

    def test_page_state_variables_and_bounded_loop(self):
        body = '''field = page.get_by_label("City")
await field.fill("Paris")
panel = page.locator(".choices").filter(visible=True)
if not await panel.is_visible():
    await field.click()
for attempt in range(3):
    if await page.get_by_text("June").is_visible():
        break
    await page.get_by_role("button", name="Next").click()
await expect(field).to_have_value("Paris")'''
        self.assertEqual(self.issues(body), [])
        self.assertEqual(runtime_required(wrap_fragment(body, 1)), 10)

    def test_next_fragment_can_reuse_successful_local_binding(self):
        statements = fragment_statements('await field.fill("Paris")', 1, {}, prefix=['field = page.get_by_label("City")'])
        self.assertEqual(len(statements), 1)
        with self.assertRaisesRegex(ValueError, '变量尚未定义'):
            fragment_statements('field = page.get_by_label("City")\nawait missing.click()', 1, {})

    def test_normalizes_locator_properties_and_does_not_reject_long_valid_batches(self):
        statements = fragment_statements('field = page.locator("input")\nawait field.first().fill("x")', 1, {})
        self.assertIn('field.first.fill', statements[-1])
        self.assertEqual(len(fragment_statements('\n'.join('await page.locator("#x").fill("x")' for _ in range(30)), 1, {})), 30)

    def test_runtime_escape_failure_swallow_and_early_return_rejected(self):
        for body in ['x = page\nawait page.goto("https://example.org")',
                     'x = page.__class__', 'x = open("secret")', 'page = params["base_url"]',
                     'x = 1\nimport os', 'x = 1\nreturn',
                     'x = 1\ntry:\n    await page.locator("#x").click()\nexcept:\n    pass',
                     'for attempt in range(999999):\n    await page.reload()',
                     'field = page.locator("#x")\nawait field.evaluate("x => x.click()")']:
            with self.subTest(body=body): self.assertTrue(self.issues(body))

    def test_regex_url_and_if_defined_names(self):
        self.assertEqual(self.issues('url = await page.title()\nawait expect(page).to_have_url(re.compile("/home"))'), [])
        self.assertTrue(self.issues('if await page.locator("#x").is_visible():\n    field = page.locator("#x")\nawait field.click()'))

    def test_regex_date_reads_and_plain_data_indexing(self):
        self.assertEqual(self.issues('''value = await page.locator("#date").input_value()
match = re.search(r"^(\\d{4})-(\\d{2})-", value)
assert match is not None
year = match.group(1)
month = value.split("-")[1]
assert year == value[:4]
assert int(month) > 0
pattern = re.compile("date", re.IGNORECASE | re.MULTILINE)
if pattern.match("DATE"):
    await expect(page.locator("#date")).to_be_visible()
box = await page.locator("#date").bounding_box()
assert box is not None
assert box["width"] > 0'''), [])

    def test_plain_data_support_does_not_allow_runtime_object_indexing(self):
        for body in ('value = page[0]', 'value = re.__dict__["compile"]',
                     'value = page.locator("#x")[0]', 'value = params["x"].__class__'):
            with self.subTest(body=body): self.assertTrue(self.issues(body))

    def test_bundle_has_standalone_code_dependencies_and_instructions(self):
        version = SimpleNamespace(code=wrap_fragment('await page.goto(params["base_url"])', 1),
            parameters={'base_url': 'https://example.org'}, origin='guided', _basic_acceptance=True, _reduced_motion=False,
            script=SimpleNamespace(snapshot={'steps': [{'step_number': 1}]}))
        with zipfile.ZipFile(io.BytesIO(bundle(version))) as z:
            self.assertEqual(set(z.namelist()), {'case.py', 'requirements.txt', 'README.md', 'case.json'})
            code = z.read('case.py').decode()
            ast.parse(code)
            self.assertIn('result.json', code)
            self.assertIn('trace.zip', code)
            self.assertNotIn('from playwright_scripts', code)
