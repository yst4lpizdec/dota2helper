(function () {
    "use strict";

    /* Число матчей читается только с разрядами. */
    function count(value) {
        return String(value).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
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

        /* Параметры показываем только у учётки, под которой сейчас Steam:
           её и проверяли, остальные — шум. Не знаем её — тогда все, как
           раньше. Номер папки человеку ничего не говорит, пишем логин. */
        if (!now.launch_ok) {
            var accounts = now.accounts || [];
            var mine = accounts.filter(function (account) {
                return now.active_login && account.login === now.active_login;
            });

            (now.active_login ? mine : accounts).forEach(function (account) {
                var line = document.createElement("div");
                line.className = "setup-step-note faint";
                line.textContent = "учётка " + (account.login || account.account)
                    + ": " + (account.options || "параметров нет");
                launchStep.body.appendChild(line);
            });

            // Под этой учёткой Доту не настраивали вовсе.
            if (now.active_login && !mine.length) {
                var none = document.createElement("div");
                none.className = "setup-step-note faint";
                none.textContent = "учётка " + now.active_login
                    + ": параметров запуска нет";
                launchStep.body.appendChild(none);
            }

            // Сам параметр — одной кнопкой в буфер, чтобы не перепечатывать.
            var copy = document.createElement("button");
            copy.className = "btn setup-btn";
            copy.type = "button";
            copy.textContent = "скопировать " + now.option;

            copy.addEventListener("click", function () {
                tell("copyText", now.option);
                copy.textContent = "скопировано";
            });

            launchStep.body.appendChild(copy);
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

                            // Число матчей в карточке «о приложении» уже другое.
                            refreshAbout();
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

    /* Дата в карточке короткая: «22 сент.», год и так понятен. */
    function shortDate(stamp) {
        if (!stamp) { return "—"; }

        return new Date(stamp * 1000).toLocaleDateString("ru-RU", {
            day: "numeric", month: "short"
        });
    }

    /* Доли с одним знаком всегда: «6%» под «7.2%» ломало столбец. */
    function pct(value) {
        return Number(value).toFixed(1) + "%";
    }

    var REFRESH_ICON =
        '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M13.2 8a5.2 5.2 0 1 1-1.5-3.7'
        + 'M13.2 2.6v2.9h-2.9" fill="none" stroke="currentColor" stroke-width="1.5"'
        + ' stroke-linecap="round" stroke-linejoin="round"/></svg>';

    /* Знаки GitHub и STRATZ — их узнают быстрее подписи. У STRATZ
       взят только замок: круг и ® на таком размере сливаются в пятно,
       а одним цветом он встаёт в ряд со знаком GitHub. */
    var GITHUB_ICON =
        '<svg viewBox="0 0 16 16" aria-hidden="true"><path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8'
        + 'c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94'
        + '-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66'
        + '.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12'
        + ' 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16'
        + ' 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01'
        + ' 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg>';

    var STRATZ_ICON =
        '<svg class="stratz" viewBox="13 12.2 33.5 33.5" aria-hidden="true"><g fill="currentColor">'
        + '<path d="M30.291 19.96l8.88 5.894v.723l-.603-.134-1.273 11.978s6.028 1.201 9.712 3.87c0 0'
        + '-6.686-2.636-13.718-2.769V32.4h1.424l-4.42-3.303V19.96zm0-4.691l1.381 1.177v2.941l1.42 1.117'
        + '.422-2.687 2.142 1.88v2.532l1.243.896.488-2.267 1.731 1.294-.4 2.875-8.427-5.816v-3.942z"/>'
        + '<path d="M29.727 19.96v9.136l-4.42 3.303h1.424v7.123c-7.032.134-13.718 2.769-13.718 2.769'
        + ' 3.684-2.669 9.711-3.87 9.711-3.87L21.45 26.444l-.602.134v-.723l8.879-5.894zm0-4.691v3.942'
        + 'l-8.428 5.816-.399-2.875 1.731-1.294.488 2.267 1.243-.896v-2.532l2.141-1.88.422 2.687 1.421'
        + '-1.117v-2.941l1.381-1.177z"/></g></svg>';

    /* «О приложении»: на чём стоят цифры и куда идти за исходниками.
       Три числа крупно, а не четыре строки «подпись … значение»: в
       списке глазу не за что зацепиться. */
    function drawAbout(now, checked) {
        var card = document.getElementById("about-card");

        if (!card) { return; }

        var fresh = !(now.data && now.data.newer) && !now.app;

        card.innerHTML =
            '<div class="card-top"><span class="card-title">О приложении</span>'
            + '<span class="about-meta"><span class="about-author">by yst4l</span>'
            + '<span class="ver-chip">v' + now.version + '</span></span></div>'
            + '<div class="about-stats">'
            + '<div><b>' + (now.patch || "—") + '</b><span>патч</span></div>'
            + '<div><b>' + count(now.matches || 0) + '</b><span>матчей</span></div>'
            + '<div><b>' + shortDate(now.built_at) + '</b><span>сбор данных</span></div>'
            + '</div>'
            + '<div class="about-foot">'
            + '<a href="https://github.com/yst4lpizdec/dota2helper" target="_blank" rel="noopener">'
            + GITHUB_ICON + 'GitHub</a>'
            + '<a href="https://stratz.com/" target="_blank" rel="noopener">'
            + STRATZ_ICON + 'Данные от STRATZ</a>'
            + '<button class="about-check" type="button">' + REFRESH_ICON
            + '<span>' + (checked && fresh ? "всё свежее" : "Проверить обновления")
            + '</span></button>'
            + '</div>';

        var check = card.querySelector(".about-check");

        if (checked && fresh) { check.classList.add("done"); }

        check.addEventListener("click", function () {
            check.disabled = true;
            check.classList.add("spin");
            check.querySelector("span").textContent = "Проверяю…";

            checkUpdates(true);
        });
    }

    function checkUpdates(force) {
        fetch("/updates" + (force ? "?force=1" : ""), { cache: "no-store" })
            .then(function (answer) { return answer.json(); })
            .then(function (now) {
                drawUpdates(now);

                drawAbout(now, force);
            })
            .catch(function (error) {
                console.error(error);

                var check = document.querySelector(".about-check");

                if (check) {
                    check.disabled = false;
                    check.classList.remove("spin");
                    check.querySelector("span").textContent = "Не вышло — ещё раз";
                }
            });
    }

    /* После скачивания данных цифры в карточке уже другие, а плашку
       обновления трогать нельзя: в ней написано «готово». */
    function refreshAbout() {
        fetch("/updates", { cache: "no-store" })
            .then(function (answer) { return answer.json(); })
            .then(function (now) { drawAbout(now); })
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
                        + '<span class="peek-share">' + pct(row.share) + "</span>"
                        + '<span class="peek-wr'
                        + (row.winrate >= 50 ? " up" : "") + '">'
                        + pct(row.winrate) + "</span>"
                        + "</div>";
                }).join("");

                peek.innerHTML =
                    '<div class="card-top"><span class="card-title">Сейчас на керри</span>'
                    + '<button class="peek-more" type="button">вся мета →</button>'
                    + "</div>"
                    + '<div class="peek-row peek-cols"><span></span><span></span><span></span>'
                    + "<span>пики</span><span>победы</span></div>"
                    + list;

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
