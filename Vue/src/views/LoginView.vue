<template>
  <div class="sgui-login" :class="{ 'is-dark': themeStore.isBlack }">
    <header class="login-topbar">
      <a class="platform-brand" href="/login" aria-label="SGUI自动化测试登录页">
        <img :src="platformIcon" alt="" width="38" height="38" />
        <span>{{ platformName }}<small>QUALITY WORKSPACE</small></span>
      </a>
      <div class="login-controls">
        <AppLocaleToggle />
        <button class="theme-button" type="button" :aria-label="themeStore.isBlack ? copy.light : copy.dark" @click="themeStore.toggleTheme">
          <icon-sun-fill v-if="themeStore.isBlack" /><icon-moon-fill v-else />
        </button>
      </div>
    </header>

    <main class="login-main">
      <section class="login-story" :aria-label="copy.overview">
        <div class="story-grid" aria-hidden="true"></div>
        <div class="story-content">
          <div class="story-eyebrow"><span></span>{{ copy.eyebrow }}</div>
          <h1>{{ copy.headlineFirst }}<br /><em>{{ copy.headlineSecond }}</em></h1>
          <p class="story-intro">{{ copy.intro }}</p>
          <div class="workflow-preview">
            <div class="workflow-heading"><span>{{ copy.workflow }}</span><small>{{ copy.illustration }}</small></div>
            <ol class="workflow-list">
              <li v-for="(step, index) in copy.steps" :key="step.title">
                <div class="workflow-icon" aria-hidden="true">
                  <icon-file v-if="index === 0" /><icon-check-circle v-else-if="index === 1" /><icon-play-arrow v-else />
                </div>
                <div><strong>{{ step.title }}</strong><p>{{ step.description }}</p></div>
                <span class="step-number">0{{ index + 1 }}</span>
              </li>
            </ol>
          </div>
          <div class="story-bottom"><span></span><p>{{ copy.tagline }}</p></div>
        </div>
      </section>

      <section class="login-panel" aria-labelledby="login-title">
        <div class="login-card">
          <div class="form-eyebrow">WELCOME TO SGUI</div>
          <h2 id="login-title">{{ copy.welcome }}</h2>
          <p class="login-description">{{ copy.description }}</p>
          <form class="login-form" :aria-busy="isLoading" @submit.prevent="handleLogin">
            <div class="form-field">
              <label for="sgui-username">{{ copy.username }}</label>
              <div class="input-wrap">
                <icon-user aria-hidden="true" />
                <input id="sgui-username" ref="usernameInput" v-model.trim="username" name="username" type="text" required
                  autocomplete="username" autocapitalize="none" :spellcheck="false" :placeholder="copy.usernamePlaceholder" :disabled="isLoading" />
              </div>
            </div>
            <div class="form-field">
              <label for="sgui-password">{{ copy.password }}</label>
              <div class="input-wrap">
                <icon-lock aria-hidden="true" />
                <input id="sgui-password" v-model="password" name="password" :type="showPassword ? 'text' : 'password'" required
                  autocomplete="current-password" :placeholder="copy.passwordPlaceholder" :disabled="isLoading"
                  :aria-describedby="capsLock ? 'caps-lock-hint' : undefined" @keydown="checkCapsLock" @keyup="checkCapsLock" @blur="capsLock = false" />
                <button class="password-toggle" type="button" :aria-label="showPassword ? copy.hidePassword : copy.showPassword"
                  :aria-pressed="showPassword" :disabled="isLoading" @click="showPassword = !showPassword">
                  <icon-eye-invisible v-if="showPassword" /><icon-eye v-else />
                </button>
              </div>
              <p v-if="capsLock" id="caps-lock-hint" class="caps-lock-hint">{{ copy.capsLock }}</p>
            </div>
            <label class="remember-account"><input v-model="rememberUsername" type="checkbox" :disabled="isLoading" /><span>{{ copy.remember }}</span></label>
            <div v-if="errorMessage" class="login-error" role="alert"><icon-exclamation-circle aria-hidden="true" /><span>{{ errorMessage }}</span></div>
            <button class="login-submit" type="submit" :disabled="isLoading">
              <span>{{ isLoading ? copy.submitting : copy.submit }}</span>
              <icon-loading v-if="isLoading" class="loading-icon" /><icon-arrow-right v-else />
            </button>
          </form>
          <div class="account-help"><icon-safe aria-hidden="true" /><span>{{ copy.accountHint }}</span></div>
          <p class="register-link">{{ copy.noAccount }} <router-link to="/register">{{ copy.register }}</router-link></p>
          <div class="form-divider"></div>
          <p class="form-note">{{ copy.note }}</p>
        </div>
      </section>
    </main>

    <footer class="login-footer">
      <span>SGUI · {{ copy.footer }}</span>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { useRoute, useRouter } from 'vue-router'
import AppLocaleToggle from '@/components/AppLocaleToggle.vue'
import { useAppI18n } from '@/composables/useAppI18n'
import { useAuthStore } from '@/store/authStore'
import { useThemeStore } from '@/store/themeStore'
import { loginDestination, platformIcon, platformName } from '@/utils/platform'

const router = useRouter(), route = useRoute(), authStore = useAuthStore(), themeStore = useThemeStore()
const { isEnglish } = useAppI18n()
const usernameInput = ref<HTMLInputElement>()
const username = ref(''), password = ref(''), rememberUsername = ref(false), showPassword = ref(false), capsLock = ref(false)
const isLoading = computed(() => authStore.getIsLoading)
const errorMessage = computed(() => authStore.getLoginError)
const copy = computed(() => isEnglish.value ? {
  overview: 'Platform overview', eyebrow: 'REQUIREMENTS · CASES · EXECUTION',
  headlineFirst: 'From requirements', headlineSecond: 'to reliable tests.',
  intro: 'Bring requirements, test cases and execution into one workspace. Keep every test connected to its purpose.',
  workflow: 'A connected testing workflow', illustration: 'Workflow preview',
  steps: [{ title: 'Organize requirements', description: 'Documents, business rules and UI references' },
    { title: 'Generate and review', description: 'AI drafts, rule checks and human review' },
    { title: 'Execute and trace', description: 'UI and API automation, records and reports' }],
  tagline: 'Clear inputs. Reviewable cases. Traceable results.',
  welcome: 'Welcome back', description: 'Sign in to continue your testing work.', username: 'Username', password: 'Password',
  usernamePlaceholder: 'Enter your username', passwordPlaceholder: 'Enter your password', remember: 'Remember username',
  showPassword: 'Show password', hidePassword: 'Hide password', capsLock: 'Caps Lock is on',
  submit: 'Enter workspace', submitting: 'Signing in…', accountHint: 'Use your existing platform account.',
  noAccount: 'New here?', register: 'Create an account', note: 'After signing in, select a project to access its requirements, cases and reports.',
  footer: 'A workspace for better testing', light: 'Switch to light theme', dark: 'Switch to dark theme',
  success: 'Signed in successfully',
} : {
  overview: '平台功能介绍', eyebrow: '需求 · 用例 · 执行', headlineFirst: '从需求出发，', headlineSecond: '让测试有据可循。',
  intro: '把需求资料、测试用例与自动化执行放在同一个工作空间，让每一次验证都有清晰的来路。',
  workflow: '连接测试的每一步', illustration: '流程示意',
  steps: [{ title: '整理需求资料', description: '汇集需求文档、业务规则与界面参考' },
    { title: '生成与评审用例', description: 'AI 生成草稿，规则校验，人工确认' },
    { title: '执行与追踪结果', description: 'UI / 接口自动化，关联执行记录与报告' }],
  tagline: '输入有依据，用例可评审，结果可追溯。',
  welcome: '欢迎回来', description: '登录 SGUI，继续你的测试工作。', username: '用户名', password: '密码',
  usernamePlaceholder: '请输入用户名', passwordPlaceholder: '请输入密码', remember: '记住用户名',
  showPassword: '显示密码', hidePassword: '隐藏密码', capsLock: '大写锁定已开启',
  submit: '进入工作台', submitting: '正在登录…', accountHint: '使用现有平台账号即可登录。',
  noAccount: '还没有账号？', register: '注册账号', note: '登录后先选择项目，即可查看对应的需求、用例和执行记录。',
  footer: '让每一次测试都有迹可循', light: '切换亮色模式', dark: '切换暗色模式', success: '登录成功',
})

function checkCapsLock(event: KeyboardEvent) { capsLock.value = event.getModifierState('CapsLock') }

async function handleLogin() {
  if (isLoading.value || !username.value || !password.value) return
  if (await authStore.login(username.value, password.value)) {
    if (rememberUsername.value) localStorage.setItem('rememberedUsername', username.value)
    else localStorage.removeItem('rememberedUsername')
    password.value = ''
    Message.success(copy.value.success)
    await router.replace(loginDestination(route.query.redirect))
  }
}

onMounted(() => {
  authStore.loginError = null
  const saved = localStorage.getItem('rememberedUsername')
  if (saved) { username.value = saved; rememberUsername.value = true }
  if (authStore.isLoggedIn) void router.replace(loginDestination(route.query.redirect))
  else usernameInput.value?.focus({ preventScroll: true })
})
</script>

<style scoped>
.sgui-login { --login-bg:#f3f6fb; --login-surface:#fff; --login-text:#172b46; --login-muted:#65768b; --login-border:#dce4ee; --login-input:#f8fafd; min-height:100dvh; box-sizing:border-box; display:flex; flex-direction:column; padding:0 clamp(20px,4.4vw,76px); background:var(--login-bg); color:var(--login-text); text-align:left; font-family:Inter,"Segoe UI","Microsoft YaHei",sans-serif; }
.sgui-login.is-dark { --login-bg:#0c1523; --login-surface:#142136; --login-text:#e9f0fb; --login-muted:#a6b7cd; --login-border:#33465e; --login-input:#101c2e; }
.sgui-login * { box-sizing:border-box; }
.login-topbar { min-height:100px; display:flex; align-items:center; justify-content:space-between; gap:20px; width:100%; max-width:1360px; margin:auto; }
.platform-brand { display:flex; align-items:center; gap:12px; color:var(--login-text); text-decoration:none; font-size:19px; font-weight:750; line-height:1.35; }
.platform-brand img { flex:none; }
.platform-brand small { display:block; margin-top:3px; color:var(--login-muted); font-size:9px; letter-spacing:2px; font-weight:500; }
.login-controls { display:flex; align-items:center; gap:16px; }
.theme-button { display:grid; place-items:center; width:34px; height:34px; border:1px solid var(--login-border); border-radius:8px; background:var(--login-surface); color:var(--login-muted); cursor:pointer; }
.login-main { display:grid; grid-template-columns:1.08fr 1fr; width:100%; max-width:1360px; margin:auto; min-height:650px; flex:1; border:1px solid var(--login-border); border-radius:18px; overflow:hidden; background:var(--login-surface); box-shadow:0 18px 55px #112f5710; }
.login-story { position:relative; display:flex; background:#102f54; color:#f4f8ff; overflow:hidden; }
.story-grid { position:absolute; inset:0; opacity:.18; background-image:linear-gradient(#84a3c329 1px,transparent 1px),linear-gradient(90deg,#84a3c329 1px,transparent 1px); background-size:44px 44px; mask-image:linear-gradient(140deg,transparent 20%,#000); pointer-events:none; }
.story-content { position:relative; z-index:1; display:flex; flex-direction:column; width:100%; padding:clamp(36px,4vw,64px); }
.story-eyebrow { display:flex; align-items:center; gap:9px; font-size:11px; letter-spacing:2px; color:#a9cbed; }
.story-eyebrow>span { width:7px; height:7px; border-radius:50%; background:#88e0ce; }
.story-content h1 { margin:32px 0 18px; font-size:clamp(30px,3.3vw,48px); line-height:1.5; letter-spacing:-1.6px; font-weight:650; }
.story-content h1 em { color:#9cc5ff; font-style:normal; }
.story-intro { max-width:420px; margin:0; font-size:14px; line-height:1.9; color:#bfd0e2; }
.workflow-preview { margin-top:35px; border:1px solid #7d9bc03d; border-radius:12px; background:#122c4a; }
.workflow-heading { padding:16px 19px; display:flex; align-items:center; justify-content:space-between; gap:10px; font-size:12px; color:#d4e3f6; border-bottom:1px solid #7d9bc026; }
.workflow-heading small { font-size:10px; color:#98b0cc; }
.workflow-list { list-style:none; margin:0; padding:7px 19px; }
.workflow-list li { position:relative; display:flex; align-items:center; gap:13px; padding:16px 0; }
.workflow-list li+li { border-top:1px solid #7d9bc022; }
.workflow-icon { display:grid; place-items:center; flex:none; width:34px; height:34px; border-radius:9px; background:#214875; font-size:18px; color:#b7d6ff; }
.workflow-list li:last-child .workflow-icon { background:#1b494e; color:#9ce1ce; }
.workflow-list strong { font-size:13px; font-weight:550; }
.workflow-list p { margin:5px 0 0; font-size:11px; color:#a8bed7; line-height:1.5; }
.step-number { margin-left:auto; padding-left:8px; font-size:12px; font-family:monospace; color:#728eaf; }
.story-bottom { display:flex; align-items:center; gap:14px; margin-top:auto; padding-top:30px; }
.story-bottom>span { width:28px; height:1px; background:#7196c2; flex:none; }
.story-bottom p { margin:0; color:#adc3dc; font-size:11px; line-height:1.6; }
.login-panel { display:flex; align-items:center; justify-content:center; padding:44px clamp(30px,5vw,86px); }
.login-card { width:100%; max-width:370px; }
.form-eyebrow { font-size:10px; letter-spacing:2px; color:#4d7ab4; font-weight:700; }
.login-card h2 { margin:15px 0 10px; color:var(--login-text); font-size:32px; font-weight:650; letter-spacing:-.5px; }
.login-description { margin:0 0 34px; font-size:14px; color:var(--login-muted); line-height:1.7; }
.form-field { margin-bottom:21px; }
.form-field>label { display:block; margin-bottom:9px; font-size:13px; font-weight:600; }
.input-wrap { display:flex; align-items:center; gap:11px; height:49px; padding:0 14px; background:var(--login-input); border:1px solid var(--login-border); border-radius:7px; color:#8696ab; transition:border-color .15s,box-shadow .15s; }
.input-wrap:focus-within { border-color:#357bdf; box-shadow:0 0 0 3px #377be316; }
.input-wrap>.arco-icon { flex:none; width:17px; height:17px; }
.input-wrap input { width:100%; min-width:0; height:100%; padding:0; border:0; outline:none; background:transparent; color:var(--login-text); font-size:14px; font-family:inherit; }
.input-wrap input::placeholder { color:#8998aa; }
.input-wrap input:disabled { opacity:.65; }
.password-toggle { flex:none; display:grid; place-items:center; width:29px; height:32px; padding:0; background:transparent; border:0; border-radius:4px; color:var(--login-muted); cursor:pointer; font-size:18px; }
.password-toggle:hover { color:#1765df; }
.caps-lock-hint { margin:7px 0 0; font-size:12px; color:#b66e12; }
.remember-account { display:flex; align-items:center; gap:8px; width:fit-content; margin:3px 0 24px; cursor:pointer; font-size:12px; color:var(--login-muted); }
.remember-account input { width:15px; height:15px; margin:0; accent-color:#1765df; }
.login-submit { width:100%; display:flex; align-items:center; justify-content:center; gap:15px; min-height:49px; padding:12px 20px; border:1px solid #1765df; border-radius:7px; background:#1765df; color:#fff; font-family:inherit; font-size:14px; font-weight:600; cursor:pointer; box-shadow:0 4px 12px #1765df20; transition:background .15s; }
.login-submit:hover { background:#1156c0; }
.login-submit:disabled { opacity:.7; cursor:wait; }
.login-error { display:flex; align-items:flex-start; gap:8px; margin:0 0 16px; padding:11px 12px; border:1px solid #eebfbf; border-radius:6px; background:#fff2f2; color:#b73131; font-size:12px; line-height:1.6; overflow-wrap:anywhere; }
.login-error>.arco-icon { flex:none; margin-top:3px; }
.account-help { display:flex; justify-content:center; align-items:center; gap:6px; margin-top:18px; font-size:11px; color:var(--login-muted); }
.register-link { margin:24px 0; text-align:center; font-size:12px; color:var(--login-muted); }
.register-link a { margin-left:4px; color:#2874d4; text-decoration:none; font-weight:600; }
.form-divider { height:1px; background:var(--login-border); }
.form-note { margin:18px 0 0; font-size:11px; color:var(--login-muted); line-height:1.8; text-align:center; }
.login-footer { display:flex; justify-content:space-between; gap:16px; max-width:1360px; width:100%; margin:auto; padding:23px 0; color:var(--login-muted); font-size:11px; line-height:1.6; }
.sgui-login button:focus-visible,.sgui-login a:focus-visible,.remember-account input:focus-visible { outline:2px solid #4284e6; outline-offset:4px; }
.loading-icon { animation:sgui-spin 1s linear infinite; }
@keyframes sgui-spin { to { transform:rotate(360deg); } }
@media(min-width:1500px) { .login-main { flex:0 1 auto; min-height:720px; } }
@media(max-width:1000px) { .login-topbar { min-height:84px; }.login-main { min-height:610px; }.story-content { padding:32px; }.login-panel { padding:34px; }.story-content h1 { font-size:32px; }.workflow-list p { font-size:10px; }.step-number { display:none; } }
@media(max-width:760px) { .sgui-login { padding:0 20px; }.login-topbar { min-height:85px; }.platform-brand { font-size:17px; gap:9px; }.platform-brand img { width:32px; height:32px; }.platform-brand small { font-size:8px; }.login-controls { gap:9px; }.login-main { display:block; flex:none; min-height:0; max-width:500px; border-radius:13px; }.login-story { display:none; }.login-panel { padding:40px 28px; }.login-card { max-width:370px; }.login-card h2 { font-size:29px; }.login-footer { flex-direction:column; align-items:center; gap:8px; margin-top:auto; padding:23px 0; text-align:center; } }
@media(prefers-reduced-motion:reduce) { .sgui-login * { animation:none!important; transition:none!important; } }
</style>
