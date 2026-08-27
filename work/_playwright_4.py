"""Playwright 截 4 张页面图：localhost:1001/video-editor 主页 + 3 个陌生 batch 详情。"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path('C:/Users/zeng/Desktop/video/work')
FRONTEND_BASE = 'http://127.0.0.1:1001'
SCREENSHOTS = [
    ('video_editor_main.png', '/video-editor?activation=demo-0815'),
    ('batch_r8_8964621101.png', '/video-editor/batch/edit-batch-2e9dc62a6c73?activation=demo-0815'),
    ('batch_stranger_education.png', '/video-editor/batch/edit-batch-stranger_education?activation=demo-0815'),
    ('batch_stranger_saas.png', '/video-editor/batch/edit-batch-stranger_saas?activation=demo-0815'),
    ('batch_stranger_logistics.png', '/video-editor/batch/edit-batch-stranger_logistics?activation=demo-0815'),
]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1280, 'height': 800})
    page = context.new_page()
    # 第一次先到 login 页面，点"进入工作台"按钮
    page.goto(FRONTEND_BASE + '/login?activation=demo-0815', wait_until='networkidle', timeout=30000)
    page.wait_for_timeout(2000)
    # 找按钮 click
    try:
        page.locator('button:has-text("进入工作台")').first.click(timeout=5000)
        page.wait_for_timeout(3000)
    except Exception as exc:
        print('  login button click failed: ' + str(exc))
    for name, path in SCREENSHOTS:
        url = FRONTEND_BASE + path
        try:
            page.goto(url, wait_until='networkidle', timeout=30000)
            page.wait_for_timeout(3000)
            out = ROOT / name
            page.screenshot(path=str(out), full_page=True)
            print('  ok: ' + name + ' size=' + str(out.stat().st_size))
        except Exception as exc:
            print('  fail: ' + name + ' -> ' + str(exc))
    browser.close()
print('done')
