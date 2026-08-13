const form = document.querySelector('#loginForm');
form.addEventListener('submit', async event => {
  event.preventDefault();
  const button = form.querySelector('button'); const error = document.querySelector('#error');
  button.disabled = true; button.textContent = 'Signing in…'; error.textContent = '';
  try {
    const response = await fetch('/api/auth/login', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(Object.fromEntries(new FormData(form))) });
    if (!response.ok) throw new Error('Invalid username or password');
    location.replace('/');
  } catch (err) { error.textContent = err.message; button.disabled = false; button.textContent = 'Sign in to HomeOps →'; }
});
