(function () {
    "use strict";

    /* Число матчей читается только с разрядами. */
    function count(value) {
        return String(value).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
    }

    /* Номер версии у STRATZ внутренний и путается с патчем Доты —
       показываем дату сбора данных. */
    function when(stamp) {
        if (!stamp) { return "неизвестно"; }

        return new Date(stamp * 1000).toLocaleDateString("ru-RU", {
            day: "numeric", month: "long", year: "numeric"
        });
    }

    /* Мост к приложению держит оболочка — страница лежит в её рамке,
       в том же окне. Свой канал здесь не завести: QWebChannel один на
       страницу, и второй просто не подключится. */
    function tell(method, arg) {
        if (window.parent && window.parent.__tell) {
            window.parent.__tell(method, arg);
        }
    }

    function bridged(run) {
        var tries = 40;

        (function wait() {
            var bridge = window.parent && window.parent.__bridge;

            if (bridge) { run(bridge); return; }

            if (tries-- > 0) { setTimeout(wait, 150); }
        }());
    }

    /* Окно теперь одного размера — подгонять высоту не нужно. */
    function reportHeight() {}

    document.getElementById("play").addEventListener("click", function () {
        tell("launchDota");
    });

    /* ---------------- готова ли Дота отдавать данные ---------------- */

    var setupBox = document.getElementById("setup");

    function step(ok, title, body) {
        var row = document.createElement("div");
        row.className = "setup-step" + (ok ? " ok" : "");

        var mark = document.createElement("span");
        mark.className = "setup-mark";
        mark.textContent = ok ? "✓" : "!";
        row.appendChild(mark);

        var text = document.createElement("div");

        var head = document.createElement("div");
        head.className = "setup-step-name";
        head.textContent = title;
        text.appendChild(head);

        if (body) {
            var note = document.createElement("div");
            note.className = "setup-step-note";
            note.innerHTML = body;
            text.appendChild(note);
        }

        row.appendChild(text);

        // Дальше в шаг иногда дописывают кнопку и мелкие строки. Класть
        // их надо в текстовую колонку, а не в саму строку: строка — это
        // «значок слева, текст справа», и всё, что попадало в неё
        // напрямую, вставало третьей колонкой сбоку.
        row.body = text;

        return row;
    }

    function drawSetup(now) {
        /* Пакеты от игры доказывают, что всё работает, лучше любой
           проверки файлов: если они идут, придираться не к чему. */
        if (now.ready && !setupForced) {
            setupBox.hidden = true;

            return;
        }

        setupBox.hidden = false;
        setupBox.innerHTML = "";

        setupBox.classList.toggle("fine", !!now.ready);

        var head = document.createElement("div");
        head.className = "setup-head";
        head.textContent = now.ready
            ? "Подключение в порядке"
            : "Dota ещё не отдаёт данные";
        setupBox.appendChild(head);

        if (!now.dota_found) {
            setupBox.appendChild(step(false, "не нашли установленную Dota 2",
                "проверьте, что игра стоит через Steam на этом компьютере"));

            return;
        }

        /* Конфиг мы умеем положить сами — это файл в папке игры. */
        var configNote = now.config_ok
            ? "лежит в папке игры"
            : "без него игра не знает, куда слать состояние матча";

        var configStep = step(now.config_ok, "конфиг GSI", configNote);

        if (!now.config_ok) {
            var button = document.createElement("button");
            button.className = "btn primary setup-btn";
            button.type = "button";
            button.textContent = "установить конфиг";

            button.addEventListener("click", function () {
                button.disabled = true;
                button.textContent = "кладу…";

                fetch("/install-config", { method: "POST", body: "{}" })
                    .then(function (answer) { return answer.json(); })
                    .then(drawSetup)
                    .catch(function (error) {
                        console.error(error);
                        button.disabled = false;
                        button.textContent = "не получилось, ещё раз";
                    });
            });

            configStep.body.appendChild(button);
        }

        setupBox.appendChild(configStep);

        /* А параметры запуска — за нас никто: их правит сам Steam, и
           лезть в его файл при запущенном Steam бесполезно, он перезапишет
           своё поверх. Поэтому показываем ровно то, что надо сделать. */
        var launchNote = now.launch_ok
            ? "уже стоит в параметрах запуска"
            : "Steam → библиотека → Dota 2 → правая кнопка → Свойства → "
              + "Параметры запуска → дописать <b>" + now.option + "</b>"
              + " и перезапустить игру";

        var launchStep = step(now.launch_ok,
            "параметр запуска " + now.option.replace(/-/g, "‑"),
            launchNote);

        /* Чужие учётки показываем, только пока параметра нет нигде:
           когда он уже стоит, список «а вот тут не стоит» — это шум. */
        if (!now.launch_ok) {
            (now.accounts || []).forEach(function (account) {
                var line = document.createElement("div");
                line.className = "setup-step-note faint";
                line.textContent = "учётка " + account.account + ": "
                    + (account.options || "параметров нет");
                launchStep.body.appendChild(line);
            });
        }

        setupBox.appendChild(launchStep);

        var tail = document.createElement("div");
        tail.className = "setup-tail";
        tail.textContent = now.ready
            ? "игра присылала данные — всё работает"
            : "как только игра пришлёт первый пакет, "
                + "эта карточка исчезнет сама";
        setupBox.appendChild(tail);
    }

    var setupForced = false;

    function checkSetup(force) {
        if (force) { setupForced = true; }

        fetch("/setup", { cache: "no-store" })
            .then(function (answer) { return answer.json(); })
            .then(drawSetup)
            .catch(function (error) { console.error(error); });
    }

    checkSetup();
    setInterval(checkSetup, 5000);

    /* Оболочка просит показать проверку подключения — по нажатию на
       состояние в боковом меню. */
    window.__showSetup = function () { checkSetup(true); };


    /* ---------------- обновления ---------------- */

    var updBox = document.getElementById("upd");

    function updRow(title, note, label, run) {
        var row = document.createElement("div");
        row.className = "upd-row";

        var text = document.createElement("div");

        var head = document.createElement("div");
        head.className = "upd-name";
        head.textContent = title;
        text.appendChild(head);

        var sub = document.createElement("div");
        sub.className = "upd-note";
        sub.textContent = note;
        text.appendChild(sub);

        row.appendChild(text);

        var button = document.createElement("button");
        button.className = "btn primary";
        button.type = "button";
        button.textContent = label;

        button.addEventListener("click", function () {
            button.disabled = true;
            button.textContent = "качаю…";

            run(button, sub);
        });

        row.appendChild(button);

        return row;
    }

    function mb(bytes) {
        return bytes ? (bytes / 1048576).toFixed(1) + " МБ" : "";
    }

    function drawUpdates(now) {
        updBox.innerHTML = "";

        var rows = [];

        if (now.data && now.data.newer) {
            rows.push(updRow(
                "Есть свежие данные",
                "новые матчи и проценты · " + mb(now.data.size),
                "обновить",
                function (button, sub) {
                    fetch("/update-data", { method: "POST", body: "{}" })
                        .then(function (answer) { return answer.json(); })
                        .then(function (done) {
                            if (done.error) { throw new Error(done.error); }

                            button.remove();
                            sub.textContent = "готово: патч " + done.patch
                                + ", " + count(done.matches) + " матчей";

                            // Число матчей в шапке уже другое.
                            loadFacts();
                        })
                        .catch(function (error) {
                            console.error(error);
                            button.disabled = false;
                            button.textContent = "ещё раз";
                        });
                }
            ));
        }

        if (now.app) {
            rows.push(updRow(
                "Новая версия " + now.app.version,
                "у тебя " + now.version + " · " + mb(now.app.size)
                    + " · программа перезапустится",
                "обновить",
                function (button, sub) {
                    fetch("/update-app", { method: "POST", body: "{}" })
                        .then(function (answer) { return answer.json(); })
                        .then(function (done) {
                            if (done.error) { throw new Error(done.error); }

                            sub.textContent = "установщик запущен";
                        })
                        .catch(function (error) {
                            console.error(error);
                            button.disabled = false;
                            button.textContent = "ещё раз";
                        });
                }
            ));
        }

        if (!rows.length) {
            updBox.hidden = true;

            return;
        }

        updBox.hidden = false;

        rows.forEach(function (row) { updBox.appendChild(row); });
    }

    /* «О приложении»: версия, на чём стоят цифры и куда идти за исходниками.
       В свёрнутом виде — только номер версии на плитке. */
    function drawAbout(now) {
        var card = document.getElementById("about-card");

        if (!card) { return; }

        card.innerHTML =
            '<div class="about-grid">'
            + '<div><span>Версия</span><b>' + now.version + '</b></div>'
            + '<div><span>Патч данных</span><b>' + (now.patch || "—") + '</b></div>'
            + '<div><span>Матчей разобрано</span><b>'
            + count(now.matches || 0) + '</b></div>'
            + '<div><span>Данные собраны</span><b>'
            + when(now.built_at) + '</b></div>'
            + '</div>'
            + '<div class="about-links">'
            + '<a href="https://github.com/yst4lpizdec/dota2helper" target="_blank" rel="noopener">Исходный код</a>'
            + '<a href="https://stratz.com/" target="_blank" rel="noopener">Данные — STRATZ</a>'
            + '</div>';

        var check = document.createElement("button");
        check.type = "button";
        check.className = "btn";
        check.textContent = "проверить обновления";

        check.addEventListener("click", function () {
            check.disabled = true;
            check.textContent = "смотрю…";

            checkUpdates(true);

            setTimeout(function () {
                check.disabled = false;
                check.textContent = "проверить обновления";
            }, 2500);
        });

        card.appendChild(check);
    }

    function checkUpdates(force) {
        fetch("/updates" + (force ? "?force=1" : ""), { cache: "no-store" })
            .then(function (answer) { return answer.json(); })
            .then(function (now) {
                drawUpdates(now);

                drawAbout(now);
            })
            .catch(function (error) { console.error(error); });
    }

    checkUpdates(false);

    /* Фон шапки. Сначала пробуем свой рисунок — он один на всё
       приложение и задаёт настроение. Если его нет, берём портрет
       героя: окно не должно оставаться пустым тёмным полем. */
    var BANNER = "/banner.png";

    function setArt(url) {
        document.getElementById("hero-art").style.backgroundImage =
            "url(" + url + ")";
    }

    function heroArt() {
        fetch("/heroes", { cache: "no-store" })
            .then(function (answer) { return answer.json(); })
            .then(function (data) {
                var list = data.heroes || [];

                if (!list.length) { return; }

                /* Герои отобраны руками: у части портретов в кадре не
                   герой, а облако эффектов — в баннере это мутное пятно. */
                var FACES = [
                    "skeleton_king", "juggernaut", "invoker",
                    "phantom_assassin", "pudge", "antimage", "lina", "axe",
                    "queenofpain", "nevermore", "dragon_knight", "mars",
                    "legion_commander", "templar_assassin", "drow_ranger",
                    "crystal_maiden"
                ];

                var known = {};
                list.forEach(function (hero) { known[hero.hero] = true; });

                var faces = FACES.filter(function (name) {
                    return known[name];
                });

                if (!faces.length) {
                    faces = list.map(function (hero) { return hero.hero; });
                }

                var left = 4;

                function attempt() {
                    if (left-- <= 0) { return; }

                    var pick = faces[Math.floor(Math.random() * faces.length)];
                    var url = "/icons/crops/" + pick + ".png";

                    var probe = new Image();
                    probe.onload = function () { setArt(url); };
                    probe.onerror = attempt;
                    probe.src = url;
                }

                attempt();
            })
            .catch(function () {});
    }

    /* Шапка отдельной функцией: после обновления данных её надо
       перерисовать — число матчей и дата уже другие. */
    function loadFacts() {
        fetch("/heroes", { cache: "no-store" })
            .then(function (answer) { return answer.json(); })
            .then(function (data) {
                document.getElementById("sub").textContent =
                    count(data.matches) + " матчей · данные от "
                    + when(data.built_at);
            })
            .catch(function () {
                document.getElementById("sub").textContent =
                    "приложение не отвечает";
            });
    }

    loadFacts();

    /* ---------------- матч прямо сейчас ---------------- */

    /* Ради этого приложение и открывают во время игры: панель может быть
       скрыта, а посмотреть, что происходит, нужно. Когда матча нет,
       карточки нет вовсе — пустая рамка «нет матча» только занимает
       место. */
    var live = document.getElementById("live");

    function drawLive(state) {
        if (!state || !state.in_match || state.waiting || !state.hero) {
            live.hidden = true;

            return;
        }

        var match = state.match || {};

        var enemies = (match.enemies || []).map(function (name) {
            return '<img src="/icons/heroes/' + name + '.png" alt="'
                + ((state.names || {})[name] || name) + '">';
        }).join("");

        var phase = (match.clock || 0) < 600
            ? "early"
            : ((match.clock || 0) < 1500 ? "core" : "late");

        var next = (state[phase] || []).filter(function (entry) {
            return !entry.owned;
        }).slice(0, 4).map(function (entry) {
            return '<div class="next-item">'
                + '<img src="/icons/items/' + entry.item + '.png" alt="">'
                + '<span>' + Math.round(entry.median_time / 60) + " мин</span>"
                + "</div>";
        }).join("");

        live.hidden = false;

        live.innerHTML =
            '<div class="live-head">'
            + '<img class="live-face" src="/icons/heroes/' + state.hero
            + '.png" alt="">'
            + '<div class="live-who"><div class="live-name">'
            + (state.hero_display || state.hero) + "</div>"
            + '<div class="live-note">' + (match.clock
                ? Math.floor(match.clock / 60) + "-я минута"
                : "матч начинается")
            + " · " + state.winrate + "% побед на этой позиции</div></div>"
            + '<div class="live-enemies">' + enemies + "</div>"
            + '<button class="btn primary" id="show-panel" type="button">'
            + "показать панель</button>"
            + "</div>"
            + (next
                ? '<div class="live-next"><span>брать дальше</span>'
                  + '<div class="next-row">' + next + "</div></div>"
                : "");

        var button = document.getElementById("show-panel");

        if (button) {
            button.addEventListener("click", function () {
                tell("showOverlay");
            });
        }
    }

    function watchMatch() {
        fetch("/state", { cache: "no-store" })
            .then(function (answer) { return answer.json(); })
            .then(drawLive)
            .catch(function () { live.hidden = true; });
    }

    watchMatch();
    setInterval(watchMatch, 3000);

    /* ---------------- что сейчас в мете ---------------- */

    /* Короткая выжимка: пятёрка самых играемых на керри. Это не навигация,
       а содержание — окно перестаёт быть пустым полем с кнопками. */
    function drawPeek() {
        var peek = document.getElementById("peek");

        if (!peek) { return; }

        fetch("/api/meta", { cache: "no-store" })
            .then(function (answer) { return answer.json(); })
            .then(function (data) {
                var rows = (data.rows || []).filter(function (row) {
                    return row.position === "POSITION_1";
                }).slice(0, 6);

                if (!rows.length) { return; }

                var list = rows.map(function (row) {
                    return '<div class="peek-row">'
                        + '<img src="/icons/heroes/' + row.hero + '.png" alt="">'
                        + '<span class="peek-name">' + row.display + "</span>"
                        + '<span class="peek-bar"><i style="width:'
                        + Math.round(row.share / rows[0].share * 100)
                        + '%"></i></span>'
                        + '<span class="peek-share">' + row.share + "%</span>"
                        + '<span class="peek-wr'
                        + (row.winrate >= 50 ? " up" : "") + '">'
                        + row.winrate + "%</span>"
                        + "</div>";
                }).join("");

                peek.innerHTML =
                    '<div class="peek-head"><span>Сейчас на керри</span>'
                    + '<button class="peek-more" type="button">вся мета</button>'
                    + "</div>" + list;

                peek.querySelector(".peek-more")
                    .addEventListener("click", function () {
                        if (window.parent && window.parent.__open) {
                            window.parent.__open("meta");
                        }
                    });
            })
            .catch(function (error) { console.error(error); });
    }

    drawPeek();

    var banner = new Image();

    banner.onload = function () {
        setArt(BANNER);
        document.body.classList.add("own-art");
    };

    banner.onerror = heroArt;
    banner.src = BANNER;

}());
