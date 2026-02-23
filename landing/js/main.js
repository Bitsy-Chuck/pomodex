// ===========================
// Nav scroll effect
// ===========================
const nav = document.getElementById('nav');

window.addEventListener('scroll', () => {
  if (window.scrollY > 40) {
    nav.classList.add('nav--scrolled');
  } else {
    nav.classList.remove('nav--scrolled');
  }
});

// ===========================
// Mobile nav toggle
// ===========================
const navToggle = document.getElementById('navToggle');

navToggle.addEventListener('click', () => {
  nav.classList.toggle('nav--open');
});

// Close mobile nav when clicking a link
document.querySelectorAll('.nav__links a').forEach(link => {
  link.addEventListener('click', () => {
    nav.classList.remove('nav--open');
  });
});

// ===========================
// Terminal typing animation
// ===========================
const commands = [
  {
    cmd: 'pomodex create my-api-server',
    output: [
      '<span class="t-green">&#10004;</span> Project <strong>my-api-server</strong> created',
      '<span class="t-dim">  Status:</span> <span class="t-green">running</span>',
      '<span class="t-dim">  SSH:</span>    ssh agent@sandbox -p 31024',
      '<span class="t-dim">  Terminal:</span> <span class="t-blue">https://pomodex.dev/t/my-api-server</span>',
    ]
  },
  {
    cmd: 'ssh agent@sandbox -p 31024',
    output: [
      '<span class="t-dim">Welcome to Ubuntu 24.04 LTS (GNU/Linux)</span>',
      '',
      '<span class="t-green">agent@my-api-server</span>:<span class="t-blue">~</span>$ node --version',
      'v22.5.0',
      '<span class="t-green">agent@my-api-server</span>:<span class="t-blue">~</span>$ python3 --version',
      'Python 3.12.4',
    ]
  },
  {
    cmd: 'pomodex snapshot my-api-server',
    output: [
      '<span class="t-dim">Creating snapshot...</span>',
      '<span class="t-green">&#10004;</span> Snapshot <span class="t-yellow">v1-2026-02-23T14:30:00Z</span> saved',
      '<span class="t-dim">  Image:</span>  pushed to registry',
      '<span class="t-dim">  Files:</span>  synced to cloud storage',
      '<span class="t-green">&#10004;</span> Restore anytime with <span class="t-blue">pomodex restore</span>',
    ]
  }
];

const typedText = document.getElementById('typedText');
const terminalOutput = document.getElementById('terminalOutput');
const cursor = document.getElementById('cursor');

let cmdIndex = 0;

function typeCommand(cmd, charIndex = 0) {
  if (charIndex < cmd.length) {
    typedText.textContent += cmd[charIndex];
    setTimeout(() => typeCommand(cmd, charIndex + 1), 35 + Math.random() * 40);
  } else {
    // Command fully typed, show output
    setTimeout(showOutput, 400);
  }
}

function showOutput() {
  const cmd = commands[cmdIndex];
  cursor.style.display = 'none';

  let html = '';
  cmd.output.forEach(line => {
    html += `<div>${line || '&nbsp;'}</div>`;
  });

  terminalOutput.innerHTML = html;
  terminalOutput.style.opacity = '0';
  terminalOutput.style.transform = 'translateY(4px)';
  requestAnimationFrame(() => {
    terminalOutput.style.transition = 'all 0.3s ease';
    terminalOutput.style.opacity = '1';
    terminalOutput.style.transform = 'translateY(0)';
  });

  // Move to next command after delay
  setTimeout(() => {
    cmdIndex = (cmdIndex + 1) % commands.length;
    typedText.textContent = '';
    terminalOutput.innerHTML = '';
    cursor.style.display = 'inline';
    typeCommand(commands[cmdIndex].cmd);
  }, 3500);
}

// Start the animation after a brief delay
setTimeout(() => {
  typeCommand(commands[0].cmd);
}, 800);

// ===========================
// Stat counter animation
// ===========================
function animateCounters() {
  const stats = document.querySelectorAll('.stat__number[data-count]');

  stats.forEach(stat => {
    if (stat.dataset.animated) return;

    const rect = stat.getBoundingClientRect();
    if (rect.top > window.innerHeight || rect.bottom < 0) return;

    stat.dataset.animated = 'true';
    const target = parseInt(stat.dataset.count, 10);
    const duration = 1500;
    const start = performance.now();

    function update(now) {
      const progress = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      const current = Math.round(eased * target);
      stat.textContent = current.toLocaleString() + '+';
      if (progress < 1) {
        requestAnimationFrame(update);
      }
    }

    requestAnimationFrame(update);
  });
}

window.addEventListener('scroll', animateCounters);
animateCounters();

// ===========================
// Smooth reveal on scroll
// ===========================
const observerOptions = {
  threshold: 0.1,
  rootMargin: '0px 0px -40px 0px'
};

const revealObserver = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      entry.target.classList.add('revealed');
      revealObserver.unobserve(entry.target);
    }
  });
}, observerOptions);

// Observe feature cards, steps, use-case cards, pricing cards, and tech items
document.querySelectorAll(
  '.feature-card, .step, .use-case-card, .pricing-card, .tech-item'
).forEach((el, i) => {
  el.style.opacity = '0';
  el.style.transform = 'translateY(20px)';
  el.style.transition = `opacity 0.5s ease ${i % 3 * 0.1}s, transform 0.5s ease ${i % 3 * 0.1}s`;
  revealObserver.observe(el);
});

// Add revealed styles
const style = document.createElement('style');
style.textContent = `.revealed { opacity: 1 !important; transform: translateY(0) !important; }`;
document.head.appendChild(style);
