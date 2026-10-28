"""Trusted replay helpers. Keep Actuator/replay_actions.py byte-identical.

No site-specific selectors or model calls. Helpers only prepare a popup, select
one explicitly scoped option, and verify the caller's original expected value.
"""
import asyncio
from playwright.async_api import expect, TimeoutError as PlaywrightTimeout


async def prepare_browser_context(context):
    """Reduce visual motion consistently during learning, replay and export.

    Keep a non-zero duration so transition/animation-end handlers still fire.
    This changes visual timing only: no forced clicks or hidden/disabled controls.
    """
    await context.add_init_script('''(() => {
      const install = () => {
        if (!document.documentElement) return false;
        const style = document.createElement('style');
        style.id = 'sgui-replay-motion';
        style.textContent = '*,*::before,*::after{animation-delay:0s!important;animation-duration:0.001s!important;transition-delay:0s!important;transition-duration:0.001s!important;scroll-behavior:auto!important}';
        document.documentElement.appendChild(style);
        return true;
      };
      if (!install()) {
        const observer = new MutationObserver(() => { if (install()) observer.disconnect(); });
        observer.observe(document, {childList:true,subtree:true});
      }
    })();''')


class ReplayStopped(Exception):
    """Observed/unknown state ended the case without claiming business success."""

class ReplayNotApplicable(ReplayStopped):
    """An observed condition explicitly makes the case inapplicable."""


PANEL_STATE = '''el => {
  const r = el.getBoundingClientRect();
  let opacity = 1, shown = r.width > 0 && r.height > 0;
  for (let node = el; node; node = node.parentElement) {
    const s = getComputedStyle(node);
    opacity *= Number(s.opacity);
    if (s.display === 'none' || s.visibility === 'hidden' || s.visibility === 'collapse'
        || node.hidden || node.getAttribute('aria-hidden') === 'true') shown = false;
  }
  return {shown: shown && opacity > 0.01,
    geometry: [r.x,r.y,r.width,r.height,opacity].map(v => Math.round(v * 100))};
}'''


class PopupActions:
    """Mixin; host recorder supplies _action_event and _recovery_evidence."""

    async def _action_event(self, message):
        pass

    async def _recovery_evidence(self, message):
        await self._action_event(message)

    async def _state_decision(self, decision):
        pass

    async def guard_state(self, *, states, outcomes, timeout_ms=6000):
        """Site-independent branching driven by observed state and the frozen case."""
        if (not isinstance(outcomes, dict) or not isinstance(states, dict)
                or outcomes.keys() != states.keys()
                or any(v not in ('continue', 'blocked', 'skip') for v in outcomes.values())):
            raise ValueError('每个状态必须明确配置 continue、blocked 或 skip。')
        await self.observe_state(states=states, timeout_ms=timeout_ms)
        matched = list(self.observed_states)
        choices = {outcomes[name] for name in matched}
        # A visible blocking overlay can coexist with its background normal form.
        # Other conflicting decisions are unknown; do not arbitrarily pick a branch.
        outcome = 'blocked' if 'blocked' in choices or len(choices) != 1 else next(iter(choices))
        reason = ('未触发／不适用：' if outcome == 'skip' else
                  '实际状态允许继续：' if outcome == 'continue' else
                  '实际状态阻塞执行：') + '、'.join(matched)
        if len(choices) > 1 and 'blocked' not in choices:
            reason = '页面状态对应的处理规则冲突，保留现场：' + '、'.join(matched)
        await self._state_decision({'matched': matched, 'outcome': outcome, 'reason': reason})
        await self._action_event(reason)
        if outcome == 'skip':
            raise ReplayNotApplicable(reason)
        if outcome == 'blocked':
            raise ReplayStopped(reason)

    async def observe_state(self, *, states, timeout_ms=6000):
        """Read-only observation. All matched states are recorded, never clicked."""
        timeout_ms = self._timeout(timeout_ms)
        if not isinstance(states, dict) or not 1 <= len(states) <= 8:
            raise ValueError('必须提供 1–8 个状态及对应定位器。')
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        previous, stable = None, 0
        while asyncio.get_running_loop().time() < deadline:
            matched = []
            for name, locator in states.items():
                count = await locator.count()
                if count > 1:
                    raise ReplayStopped(f'状态“{name}”匹配多个元素，无法可靠判断，请限定定位范围。')
                if count and (await self._panel_state(locator))['shown']:
                    matched.append(name)
            # Two identical observations avoid recording a single animation frame.
            stable = stable + 1 if matched and matched == previous else 0
            if stable >= 2:
                self.observed_states = matched
                await self._action_event('实际页面状态：' + '、'.join(matched) + '；页面：' + self.page.url)
                return
            previous = matched
            await asyncio.sleep(0.15)
        self.observed_states = []
        raise ReplayStopped('未识别到用例列出的页面状态，保留现场并停止，不能判定为通过。')

    async def stop(self, *, reason):
        states = getattr(self, 'observed_states', [])
        message = reason + ('；最近观察到：' + '、'.join(states) if states else '')
        await self._action_event('按用例要求停止：' + message)
        raise ReplayStopped(message)

    async def _panel_state(self, panel):
        count = await panel.count()
        if count > 1:
            # Many apps keep inactive popups mounted. Hidden copies are not
            # ambiguous; two actually displayed panels still need scoping.
            states = await panel.evaluate_all('elements => elements.map(' + PANEL_STATE + ').filter(state => state.shown)')
            if len(states) > 1:
                raise ValueError('浮层定位器匹配多个可见面板，请限定所属容器。')
            return states[0] if states else {'shown': False, 'geometry': []}
        if not count:
            return {'shown': False, 'geometry': []}
        try:
            return await panel.evaluate(PANEL_STATE, timeout=1000)
        except PlaywrightTimeout:
            return {'shown': False, 'geometry': []}

    def _timeout(self, timeout_ms):
        if type(timeout_ms) is not int or not 500 <= timeout_ms <= 15000:
            raise ValueError('公共操作等待时间须为 500–15000 毫秒。')
        return timeout_ms

    async def _unique(self, locator, label, timeout_ms):
        try:
            await expect(locator).to_have_count(1, timeout=timeout_ms)
        except AssertionError as exc:
            if await locator.count() > 1:
                raise ValueError(label + '匹配多个元素，请限定范围。') from exc
            raise PlaywrightTimeout(label + '没有出现。') from exc

    async def ensure_open(self, *, trigger, panel, label='选择面板', timeout_ms=6000):
        """Do not toggle an already-open panel. Wait for real opacity/geometry."""
        timeout_ms = self._timeout(timeout_ms)
        await self._unique(trigger, label + '入口', timeout_ms)
        state = await self._panel_state(panel)
        if state['shown']:
            await self._action_event(label + '：面板已打开，保留当前状态。')
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        previous = None
        clicked = False
        while asyncio.get_running_loop().time() < deadline:
            state = await self._panel_state(panel)
            # A field value can update before the previous popup's closing
            # animation ends. If it disappears while we prepare, open it once.
            # Never click a still-open panel, nor repeatedly toggle an entrance.
            if not state['shown'] and not clicked:
                await self._action_event(label + '：面板已收起，点击入口打开面板。')
                await trigger.click(timeout=timeout_ms)
                clicked = True
                previous = None
                continue
            if state['shown'] and state['geometry'] == previous:
                return
            previous = state['geometry'] if state['shown'] else None
            await asyncio.sleep(0.1)
        raise PlaywrightTimeout(label + '未保持打开或动画未稳定。')

    async def expect_closed(self, *, panel, label='选择面板', timeout_ms=6000):
        """Accept removed/display:none/ancestor hidden/transparent panels alike."""
        deadline = asyncio.get_running_loop().time() + self._timeout(timeout_ms) / 1000
        while asyncio.get_running_loop().time() < deadline:
            if not (await self._panel_state(panel))['shown']:
                return
            await asyncio.sleep(0.1)
        raise AssertionError(label + '仍然展开，不符合收起预期。')

    async def dismiss_panel(self, *, panel, dismiss, label='选择面板', timeout_ms=6000):
        """Idempotent explicit dismissal; never force through an overlay."""
        timeout_ms = self._timeout(timeout_ms)
        if (await self._panel_state(panel))['shown']:
            await self._unique(dismiss, label + '关闭入口', timeout_ms)
            await dismiss.click(timeout=timeout_ms)
            await self._action_event(label + '：已使用指定关闭入口收起。')
        await self.expect_closed(panel=panel, label=label, timeout_ms=timeout_ms)

    async def set_toggle(self, *, control, selected, unselected, checked, label='选择项', timeout_ms=6000):
        """Idempotently set a custom checkbox using explicit observed state markers.

        Both markers missing or present is unknown, never assumed unchecked.
        Only an actual mismatched state triggers one ordinary click.
        """
        timeout_ms = self._timeout(timeout_ms)
        if type(checked) is not bool:
            raise ValueError('checked 必须为布尔值。')
        await self._unique(control, label, timeout_ms)
        await expect(control).to_be_visible(timeout=timeout_ms)
        async def state():
            yes, no = await self._panel_state(selected), await self._panel_state(unselected)
            if yes['shown'] == no['shown']:
                return None
            return yes['shown']
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        current = await state()
        while current is None and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(.1)
            current = await state()
        if current is None:
            raise ReplayStopped(label + '选中状态未知或冲突，未点击。')
        if current != checked:
            await control.click(timeout=timeout_ms)
            deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
            while await state() != checked and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(.1)
            if await state() != checked:
                raise AssertionError(label + '点击后状态不符合原预期。')
            await self._action_event(label + '：已点击并核对目标选中状态。')
        else:
            await self._action_event(label + '：当前已符合目标选中状态，无需反向点击。')

    async def expect_aligned(self, *, target, reference, axis='column', tolerance_px=8, timeout_ms=6000):
        """Verify a real row/column relationship (e.g. a date and weekday header)."""
        if axis not in ('column','row') or type(tolerance_px) is not int or not 0<=tolerance_px<=20:
            raise ValueError('对齐方向须为 column/row，容差须为 0–20 像素。')
        timeout_ms=self._timeout(timeout_ms)
        await self._unique(target,'被核对元素',timeout_ms)
        await self._unique(reference,'参考元素',timeout_ms)
        await expect(target).to_be_visible(timeout=timeout_ms)
        await expect(reference).to_be_visible(timeout=timeout_ms)
        deadline=asyncio.get_running_loop().time()+timeout_ms/1000
        previous=None
        while asyncio.get_running_loop().time()<deadline:
            a,b=await target.bounding_box(),await reference.bounding_box()
            if a and b:
                dx=abs(a['x']+a['width']/2-b['x']-b['width']/2)
                dy=abs(a['y']+a['height']/2-b['y']-b['height']/2)
                boxes=(a,b)
                aligned=(dx<=tolerance_px and dy>2) if axis=='column' else (dy<=tolerance_px and dx>2)
                if aligned and boxes==previous:
                    await self._action_event(f'位置关系已核对：{axis}，水平差 {dx:.1f}px，垂直差 {dy:.1f}px。')
                    return
                previous=boxes
            await asyncio.sleep(.1)
        raise AssertionError('实际元素位置不符合原预期的同行/同列关系，未修改预期。')

    async def choose_option(self, *, trigger, panel, value, expected_value,
                            option_name=None, option_role='option', option_selector=None,
                            value_mode='value', label='选项', timeout_ms=6000):
        """At most one reopen, only before a successful selection click.

        Only use for idempotent field choices (city/date/dropdown), never submit,
        order, delete or payment buttons. Post-selection assertions never retry.
        """
        timeout_ms = self._timeout(timeout_ms)
        if bool(option_name) == bool(option_selector):
            raise ValueError('候选项须提供 option_name 或 option_selector，且只能提供一种。')
        if not isinstance(expected_value, str):
            raise ValueError('选值后的预期必须是明确的文本值。')
        if value_mode not in ('value', 'text', 'text_contains'):
            raise ValueError('选值验证模式必须是 value、text 或 text_contains。')
        await self._unique(value, label + '结果输入框', timeout_ms)
        for attempt in (1, 2):
            phase = '打开面板'
            try:
                await self.ensure_open(trigger=trigger, panel=panel, label=label, timeout_ms=timeout_ms)
                # Build the option *inside* the supplied panel, never on the page.
                visible_panel = panel.filter(visible=True)
                option = (visible_panel.locator(option_selector) if option_selector else
                          visible_panel.get_by_role(option_role, name=option_name, exact=True)).filter(visible=True)
                phase = '定位候选项'
                await self._unique(option, label + '候选项', timeout_ms)
                phase = '点击候选项'
                await option.click(timeout=min(timeout_ms, 4000))
            except PlaywrightTimeout as exc:
                closed = not (await self._panel_state(panel))['shown']
                failure = f'{label}：第 {attempt} 次{phase}超时，' + ('面板已关闭。' if closed else '面板仍打开。')
                await self._action_event(failure + '\n' + str(exc)[:1500])
                if attempt == 1 and closed:
                    await self._recovery_evidence(label + '：保存失败现场，重新打开面板，仅恢复一次。')
                    continue
                raise
            # Keep outside the recoverable click block: a wrong value is a real
            # assertion failure and must not be hidden by repeating the action.
            assertion = {'value':'to_have_value', 'text':'to_have_text', 'text_contains':'to_contain_text'}[value_mode]
            await getattr(expect(value), assertion)(expected_value, timeout=timeout_ms)
            await self._action_event(label + '：选择后输入值与预期一致。')
            return
