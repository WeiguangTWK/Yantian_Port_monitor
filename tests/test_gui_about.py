"""关于页静态回归检查，不导入 Qt、不联网。"""

import ast
import pathlib
import unittest
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent


class TestAboutPage(unittest.TestCase):
    def test_author_and_logo_resource(self):
        source = (ROOT / 'gui/about.py').read_text('utf-8')
        self.assertIn('作者 WeiguangTWK · 新桂轮未来生产实验室', source)
        self.assertIn('QtCore.Qt.KeepAspectRatio', source)
        self.assertTrue((ROOT / 'gui/assets/newguilun_logo.jpg').is_file())

    def test_source_repository_constant(self):
        tree = ast.parse((ROOT / 'gui/about.py').read_text('utf-8'))
        assignments = {target.id: node.value.value for node in tree.body
                       if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                       for target in node.targets if isinstance(target, ast.Name)}
        self.assertEqual(assignments['SOURCE_URL'],
                         'https://github.com/WeiguangTWK/Yantian_Port_monitor')

    def test_offline_legal_documents_exist(self):
        for relative in ('LICENSE', 'licenses/LGPL-3.0.txt', 'licenses/THIRD_PARTY_NOTICES.md'):
            self.assertTrue((ROOT / relative).is_file())
            self.assertTrue((ROOT / relative).read_text('utf-8').strip())

    def test_icon_is_valid_svg(self):
        svg = ET.parse(ROOT / 'gui/assets/about.svg').getroot()
        self.assertEqual(svg.tag, '{http://www.w3.org/2000/svg}svg')

    def test_page_is_registered(self):
        source = (ROOT / 'gui/app.py').read_text('utf-8')
        self.assertIn('self.about_page = AboutPage(self)', source)
        self.assertIn('self.about_page.setObjectName("aboutPage")', source)
