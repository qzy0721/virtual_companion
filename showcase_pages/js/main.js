/** Lightweight interactions for the static project showcase. */
(function () {
  'use strict';

  const nav = document.querySelector('.nav');
  const toggle = document.querySelector('.nav-toggle');
  const navLinks = document.querySelector('.nav-links');
  const reveals = document.querySelectorAll('.reveal');
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const progress = document.createElement('div');
  progress.className = 'scroll-progress';
  document.body.appendChild(progress);

  if (toggle && navLinks) {
    const setOpen = (open) => {
      navLinks.classList.toggle('open', open);
      toggle.setAttribute('aria-expanded', String(open));
    };
    toggle.addEventListener('click', () => setOpen(!navLinks.classList.contains('open')));
    navLinks.querySelectorAll('a').forEach((link) => link.addEventListener('click', () => setOpen(false)));
  }

  const revealObserver = 'IntersectionObserver' in window
    ? new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          entry.target.classList.add('visible');
          revealObserver.unobserve(entry.target);
        });
      }, { threshold: 0.08 })
    : null;

  reveals.forEach((element) => {
    // 同一容器内的 reveal 元素按序错峰出现
    const siblings = Array.from(element.parentElement.children).filter((child) => child.classList && child.classList.contains('reveal'));
    const index = siblings.indexOf(element);
    if (index > 0) element.style.setProperty('--reveal-delay', `${Math.min(index * 80, 480)}ms`);
    if (revealObserver) revealObserver.observe(element);
    else element.classList.add('visible');
  });

  function updateScrollState() {
    if (nav) nav.classList.toggle('scrolled', window.scrollY > 8);
    const maximum = document.documentElement.scrollHeight - window.innerHeight;
    progress.style.transform = `scaleX(${maximum > 0 ? Math.min(window.scrollY / maximum, 1) : 0})`;
  }

  window.addEventListener('scroll', updateScrollState, { passive: true });
  updateScrollState();

  if (!reducedMotion) {
    const parallaxElements = document.querySelectorAll('[data-parallax]');
    const updateParallax = () => {
      parallaxElements.forEach((element) => {
        const speed = Number(element.dataset.parallax || 0.1);
        element.style.transform = `translate3d(0, ${window.scrollY * speed}px, 0)`;
      });
    };
    window.addEventListener('scroll', updateParallax, { passive: true });
    updateParallax();

    document.querySelectorAll('.card, .doc-card, .download-card').forEach((card) => {
      card.addEventListener('mousemove', (event) => {
        const bounds = card.getBoundingClientRect();
        card.style.setProperty('--spot-x', `${event.clientX - bounds.left}px`);
        card.style.setProperty('--spot-y', `${event.clientY - bounds.top}px`);
      });
    });

    // 轻微的 3D 倾斜，让卡片与演示窗跟随指针
    document.querySelectorAll('.card, .doc-card, .demo-window').forEach((element) => {
      const strength = element.classList.contains('demo-window') ? 4.5 : 3;
      element.addEventListener('mousemove', (event) => {
        const bounds = element.getBoundingClientRect();
        const px = (event.clientX - bounds.left) / bounds.width - .5;
        const py = (event.clientY - bounds.top) / bounds.height - .5;
        element.style.transform = `perspective(950px) rotateX(${(-py * strength).toFixed(2)}deg) rotateY(${(px * strength).toFixed(2)}deg) translateY(-4px)`;
      });
      element.addEventListener('mouseleave', () => { element.style.transform = ''; });
    });

    // Hero 氛围光斑跟随鼠标轻微漂移（translate 属性与 transform 动画互不冲突）
    const hero = document.querySelector('.hero');
    if (hero) {
      const orbs = hero.querySelectorAll('.hero-orb');
      const depths = [30, 46, 22];
      hero.addEventListener('mousemove', (event) => {
        const cx = event.clientX / window.innerWidth - .5;
        const cy = event.clientY / window.innerHeight - .5;
        orbs.forEach((orb, index) => {
          const depth = depths[index] || 26;
          orb.style.translate = `${(cx * depth).toFixed(1)}px ${(cy * depth).toFixed(1)}px`;
        });
      }, { passive: true });
    }

    // 演示窗中的流式回复打字机
    const demoText = document.getElementById('demo-text');
    if (demoText) {
      const phrases = [
        '好呀，今天想聊点什么？我一直都在桌面上等你呢。',
        '刚刚检测到你在敲代码，要不要休息一下？',
        '我记得你上次说喜欢这首歌，要现在放吗？'
      ];
      let phraseIndex = 0;
      let charIndex = 0;
      let deleting = false;
      const tick = () => {
        const phrase = phrases[phraseIndex];
        charIndex += deleting ? -1 : 1;
        demoText.textContent = phrase.slice(0, charIndex);
        let delay = deleting ? 28 : 95;
        if (!deleting && charIndex === phrase.length) { deleting = true; delay = 2200; }
        else if (deleting && charIndex === 0) {
          deleting = false;
          phraseIndex = (phraseIndex + 1) % phrases.length;
          delay = 500;
        }
        window.setTimeout(tick, delay);
      };
      window.setTimeout(tick, 900);
    }

    document.querySelectorAll('.btn').forEach((button) => {
      button.addEventListener('mousemove', (event) => {
        const bounds = button.getBoundingClientRect();
        const x = (event.clientX - bounds.left - bounds.width / 2) * .1;
        const y = (event.clientY - bounds.top - bounds.height / 2) * .1;
        button.style.transform = `translate(${x}px, ${y - 2}px)`;
      });
      button.addEventListener('mouseleave', () => { button.style.transform = ''; });
    });

    const glow = document.createElement('div');
    glow.className = 'cursor-glow';
    document.body.appendChild(glow);
    let glowTimer;
    document.addEventListener('mousemove', (event) => {
      glow.style.left = `${event.clientX}px`;
      glow.style.top = `${event.clientY}px`;
      glow.classList.add('active');
      window.clearTimeout(glowTimer);
      glowTimer = window.setTimeout(() => glow.classList.remove('active'), 700);
    }, { passive: true });

    // 链路演示：随滚动逐阶段推进
    const flowDemo = document.getElementById('flow-demo');
    if (flowDemo) initFlowDemo(flowDemo);
  } else {
    // 减少动态：静态点亮全部阶段
    const flowDemoStatic = document.getElementById('flow-demo');
    if (flowDemoStatic) {
      flowDemoStatic.querySelectorAll('.flow-node').forEach((node) => node.classList.add('active'));
      flowDemoStatic.querySelectorAll('.flow-link').forEach((link) => link.classList.add('filled'));
    }
  }

  function initFlowDemo(flowDemo) {
    const nodes = Array.from(flowDemo.querySelectorAll('.flow-node'));
    const links = Array.from(flowDemo.querySelectorAll('.flow-link'));
    const caption = document.getElementById('flow-caption');
    const captions = [
      '按住说话或右键桌宠，声音以 PCM 流实时送出。',
      '流式 ASR 边听边转写，说完即得到完整句子。',
      '角色人格、对话历史与长期记忆一起进入上下文。',
      '回复逐句生成，完整句子立刻进入语音合成队列。',
      '声音播放的同时，口型、表情与动作同步驱动 VRM。'
    ];
    let flowIndex = -1;
    const applyFlow = (next) => {
      if (next === flowIndex) return;
      flowIndex = next;
      nodes.forEach((node, i) => {
        node.classList.toggle('active', i === flowIndex);
        node.classList.toggle('passed', i < flowIndex);
      });
      links.forEach((link, i) => link.classList.toggle('filled', i < flowIndex));
      if (caption) {
        caption.classList.add('swap');
        window.setTimeout(() => {
          caption.textContent = captions[flowIndex];
          caption.classList.remove('swap');
        }, 200);
      }
    };
    // 滚轮驱动：区块随滚动逐阶段推进，经过的节点保留“已通过”状态
    const updateFlow = () => {
      const rect = flowDemo.getBoundingClientRect();
      const vh = window.innerHeight;
      const start = vh * .88;
      const total = rect.height + vh * .42;
      const progress = Math.min(Math.max((start - rect.top) / total, 0), .999);
      applyFlow(Math.floor(progress * nodes.length));
    };
    window.addEventListener('scroll', updateFlow, { passive: true });
    window.addEventListener('resize', updateFlow);
    updateFlow();
  }

  const counters = document.querySelectorAll('[data-count]');
  if (counters.length) {
    const counterObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        const element = entry.target;
        const target = Number(element.dataset.count);
        if (reducedMotion) {
          element.textContent = String(target);
        } else {
          const started = performance.now();
          const animate = (now) => {
            const progressValue = Math.min((now - started) / 1200, 1);
            const eased = 1 - Math.pow(1 - progressValue, 3);
            element.textContent = String(Math.round(target * eased));
            if (progressValue < 1) window.requestAnimationFrame(animate);
          };
          window.requestAnimationFrame(animate);
        }
        counterObserver.unobserve(element);
      });
    }, { threshold: .45 });
    counters.forEach((counter) => counterObserver.observe(counter));
  }

  document.querySelectorAll('a[href^="#"]').forEach((link) => {
    link.addEventListener('click', (event) => {
      const id = link.getAttribute('href');
      if (!id || id === '#') return;
      const target = document.querySelector(id);
      if (!target) return;
      event.preventDefault();
      target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });

  // 跨页面锚点在 sticky 导航下需要主动留出顶部空间；延后一帧也能覆盖
  // 浏览器在 reload 后恢复旧滚动位置的情况。
  if (window.location.hash) {
    window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
      const target = document.querySelector(window.location.hash);
      if (target) target.scrollIntoView({ behavior: 'auto', block: 'start' });
    }));
  }
})();
