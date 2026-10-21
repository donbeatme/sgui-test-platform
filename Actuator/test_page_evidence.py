import asyncio
import json
import unittest
from playwright.async_api import async_playwright
from page_evidence import capture_page_evidence


class EvidencePriorityTests(unittest.TestCase):
    def test_large_background_does_not_hide_open_panel_or_selected_state(self):
        async def run():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    page = await browser.new_page()
                    background = ''.join(f'<li id="nav{i}">Navigation {i}</li>' for i in range(1000))
                    days = ''.join(f'<td data-testid="day-{i}" class="{"selected" if i == 28 else "day"}">{i}</td>' for i in range(1, 32))
                    await page.set_content(f'<input id="value" value="D"><button id="open" onclick="document.querySelector(\'#panel\').hidden=false">Open</button><ul>{background}</ul><div id="panel" role="dialog" hidden><table><tr>{days}</tr></table></div>')
                    initial = json.loads((await capture_page_evidence(page))['dom'])
                    known = [n['css'] for n in initial['nodes'] if n['visible']]
                    await page.locator('#open').click()
                    for _ in range(2):
                        evidence = await capture_page_evidence(page, known_visible=known)
                        dom = json.loads(evidence['dom'])
                        self.assertTrue(dom['truncated'])
                        selected = next(n for n in dom['nodes'] if n['attrs'].get('data-testid') == 'day-28')
                        self.assertTrue(selected['visible'])
                        self.assertEqual(selected['attrs']['class'], 'selected')
                        self.assertIn('backgroundColor', selected['styles'])
                        self.assertTrue(any(n['attrs'].get('id') == 'panel' for n in dom['nodes']))
                        self.assertTrue(any(n['attrs'].get('id') == 'value' for n in dom['nodes']))
                        self.assertLessEqual(len(evidence['dom']), 39000)
                    # No baseline is needed for a replay failure snapshot: the
                    # visible dialog/selection still outranks background nodes.
                    final = json.loads((await capture_page_evidence(page))['dom'])
                    self.assertTrue(any(n['attrs'].get('data-testid') == 'day-28' for n in final['nodes']))
                finally:
                    await browser.close()
        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
