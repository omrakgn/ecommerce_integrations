// Withdrawal function ("Vertrag widerrufen", § 356a BGB / Art. 11a Directive 2011/83/EU).
// The button opens the form (snippets/withdrawal-dialog.liquid) in a window over the current
// page; "Widerruf bestätigen" sends the declaration to ERPNext
// (multichannel_core.returns.withdrawal.submit), which keeps it, sends the acknowledgement of
// receipt by email at once and opens the return case. The answer shows in the same window.
// A link to "#vertrag-widerrufen" opens the window too.
// Rules that must stay: no login, no CAPTCHA, the reason stays optional.
(function () {
  if (window.withdrawalReady) return;
  window.withdrawalReady = true;
  var HASH = '#vertrag-widerrufen';
  var SELECTOR = 'dialog[data-withdrawal]';
  var opener = null;

  function fill(text, values) {
    return (text || '').replace(/\{(\w+)\}/g, function (match, key) {
      return values[key] != null ? values[key] : '';
    });
  }

  function make(tag, attrs, text) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) { node.setAttribute(key, attrs[key]); });
    if (text) node.textContent = text;
    return node;
  }

  function texts(dialog) {
    if (!dialog.wdTexts) {
      try {
        dialog.wdTexts = JSON.parse(dialog.querySelector('script[data-withdrawal-texts]').textContent);
      } catch (e) {
        dialog.wdTexts = {};
      }
    }
    return dialog.wdTexts;
  }

  function open(dialog, from) {
    if (!dialog || dialog.open) return;
    opener = from || null;
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
    document.documentElement.classList.add('withdrawal-lock');
    var title = dialog.querySelector('.withdrawal-title');
    if (title) title.focus();
  }

  function close(dialog) {
    if (!dialog) return;
    if (typeof dialog.close === 'function') return dialog.close();
    dialog.removeAttribute('open');
    closed(dialog);
  }

  function toggleItems(form) {
    var checked = form.querySelector('input[name="scope"]:checked');
    var some = !!checked && checked.value === 'some';
    var box = form.querySelector('[data-withdrawal-items]');
    box.hidden = !some;
    box.querySelector('textarea').required = some;
  }

  // After the window closes: page scrolls again, focus goes back to the button, an old error
  // goes away (what was typed stays). After a declaration the next opening starts empty.
  function closed(dialog) {
    document.documentElement.classList.remove('withdrawal-lock');
    if (window.location.hash === HASH && window.history.replaceState) {
      window.history.replaceState(null, '', window.location.pathname + window.location.search);
    }
    var status = dialog.querySelector('.withdrawal-status');
    status.className = 'withdrawal-status';
    status.textContent = '';
    if (dialog.getAttribute('data-done')) {
      dialog.removeAttribute('data-done');
      var form = dialog.querySelector('form');
      form.reset();
      form.hidden = false;
      toggleItems(form);
      dialog.querySelector('.withdrawal-intro').hidden = false;
    }
    if (opener && opener.focus) opener.focus();
    opener = null;
  }

  function submit(form) {
    var dialog = form.closest(SELECTOR);
    var t = texts(dialog);
    var status = dialog.querySelector('.withdrawal-status');
    var button = form.querySelector('.withdrawal-submit');
    var fallback = dialog.getAttribute('data-fallback');
    if (!form.checkValidity()) {
      form.reportValidity();
      return;
    }

    function showError() {
      status.className = 'withdrawal-status is-error';
      status.textContent = fallback ? fill(t.error, { fallback: fallback }) : t.error_retry;
      button.disabled = false;
      button.textContent = t.confirm;
    }

    var data = new FormData(form);
    var body = new URLSearchParams();
    data.forEach(function (value, key) { body.append(key, value); });
    body.append('language', (dialog.getAttribute('data-lang') || 'en').slice(0, 2));
    try { body.append('time_zone', Intl.DateTimeFormat().resolvedOptions().timeZone || ''); } catch (e) {}
    body.append('page', window.location.href.split(/[?#]/)[0]);
    button.disabled = true;
    button.textContent = t.sending;
    status.className = 'withdrawal-status';
    status.textContent = '';
    // No cookies: the declaration needs no login and must not depend on one.
    fetch(dialog.getAttribute('data-endpoint'), { method: 'POST', body: body, credentials: 'omit' })
      .then(function (response) {
        return response.json().then(function (json) { return { ok: response.ok, json: json }; });
      })
      .then(function (result) {
        var answer = result.json && result.json.message;
        if (!result.ok || !answer || !answer.ok) return showError();
        var done = make('div', { class: 'withdrawal-done' });
        var heading = make('h3', { tabindex: '-1' }, t.done_title);
        done.appendChild(heading);
        done.appendChild(make('p', {}, fill(t.done, {
          date: answer.received_at || '',
          email: data.get('email'),
          ref: answer.reference || ''
        })));
        if (answer.found === false) {
          done.appendChild(make('p', { class: 'withdrawal-note' }, fill(t.not_found, {
            order: data.get('order_reference'),
            contact: answer.contact_email || fallback
          })));
        }
        done.appendChild(make('button', { type: 'button', class: 'withdrawal-submit', 'data-withdrawal-close': '' }, t.close));
        button.disabled = false;
        button.textContent = t.confirm;
        form.hidden = true;
        dialog.querySelector('.withdrawal-intro').hidden = true;
        dialog.setAttribute('data-done', '1');
        status.appendChild(done);
        heading.focus();
      })
      .catch(showError);
  }

  var pressedOutside = false;
  document.addEventListener('pointerdown', function (event) {
    pressedOutside = !!(event.target && event.target.matches && event.target.matches(SELECTOR));
  });

  document.addEventListener('click', function (event) {
    var target = event.target;
    if (!target || !target.closest) return;
    var trigger = target.closest('[data-withdrawal-open]');
    if (trigger) {
      open(trigger.parentNode.querySelector(SELECTOR), trigger);
      return;
    }
    var closer = target.closest('[data-withdrawal-close]');
    if (closer) {
      close(closer.closest(SELECTOR));
      return;
    }
    // A click on the dark area around the window closes it (not a text selection that ends there).
    if (target.matches(SELECTOR) && pressedOutside) {
      close(target);
      return;
    }
    var link = target.closest('a[href]');
    if (link && link.hash === HASH && link.host === window.location.host) {
      var dialog = document.querySelector(SELECTOR);
      if (dialog) {
        event.preventDefault();
        open(dialog, link);
      }
    }
  });

  // "close" does not bubble; it reaches the document while capturing.
  document.addEventListener('close', function (event) {
    if (event.target.matches && event.target.matches(SELECTOR)) closed(event.target);
  }, true);

  document.addEventListener('change', function (event) {
    var form = event.target.form;
    if (form && form.closest(SELECTOR)) toggleItems(form);
  });

  document.addEventListener('submit', function (event) {
    var form = event.target;
    if (!form.closest || !form.closest(SELECTOR)) return;
    event.preventDefault();
    submit(form);
  });

  function fromHash() {
    if (window.location.hash === HASH) open(document.querySelector(SELECTOR), null);
  }
  window.addEventListener('hashchange', fromHash);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fromHash);
  else fromHash();
})();
