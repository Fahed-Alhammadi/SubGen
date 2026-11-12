// Sign-in form: posts form data to /api/login and shows the server's error detail.
const form = document.getElementById('login-form');
const errorEl = document.getElementById('login-error');
const submit = form.querySelector('[type=submit]');

function showError(message) {
  errorEl.textContent = message;
  errorEl.hidden = false;
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  errorEl.hidden = true;
  const username = form.username.value.trim();
  const password = form.password.value;
  const totp = form.totp_code.value.trim();
  if (!username || !password) {
    showError('Enter your username and password.');
    (username ? form.password : form.username).focus();
    return;
  }
  if (totp && !/^\d{6}$/.test(totp)) {
    showError('The authenticator code is six digits.');
    form.totp_code.focus();
    return;
  }
  submit.disabled = true;
  submit.textContent = 'Signing in…';
  try {
    const body = new FormData();
    body.append('username', username);
    body.append('password', password);
    body.append('totp_code', totp);
    const response = await fetch('/api/login', { method: 'POST', body, credentials: 'same-origin', headers: { accept: 'application/json' } });
    if (response.ok) {
      window.location.assign('/');
      return;
    }
    let detail = '';
    try {
      const data = await response.json();
      detail = typeof data.detail === 'string' ? data.detail : '';
    } catch {
      detail = '';
    }
    if (response.status === 429) showError(detail || 'Too many sign-in attempts. Wait a minute and try again.');
    else showError(detail || 'Sign-in failed. Check your details and try again.');
    form.password.select();
  } catch {
    showError('Cannot reach the SubGen server. Check that it is running.');
  }
  submit.disabled = false;
  submit.textContent = 'Sign in';
});
