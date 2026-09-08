document.addEventListener("DOMContentLoaded", function () {
    initTabs();
    initPlaatsToevoegen("plaats-toevoegen", "nieuwe-plaats", "#panel-woningen .checkbox-grid", "plaats");
    initPlaatsToevoegen("business-plaats-toevoegen", "business-nieuwe-plaats", "#business-regio-grid", "business_plaats");
    initSelectieKnoppen();
    initNieuweScanValidatie();
    initNieuweBusinessScanValidatie();
    initSorteerbareTabel();
});

function initTabs() {
    const tabs = document.querySelectorAll(".tab");
    const typeInput = document.getElementById("type-input");
    const panelWoningen = document.getElementById("panel-woningen");
    const panelBedrijfsmatig = document.getElementById("panel-bedrijfsmatig");

    if (!tabs.length || !typeInput || !panelWoningen || !panelBedrijfsmatig) {
        return;
    }

    tabs.forEach(function (tab) {
        tab.addEventListener("click", function () {
            tabs.forEach(function (t) {
                t.classList.remove("active");
            });
            tab.classList.add("active");

            const type = tab.dataset.type;
            typeInput.value = type;

            if (type === "bedrijfsmatig") {
                panelWoningen.classList.add("hidden");
                panelBedrijfsmatig.classList.remove("hidden");
            } else {
                panelBedrijfsmatig.classList.add("hidden");
                panelWoningen.classList.remove("hidden");
            }
        });
    });
}

function initPlaatsToevoegen(knopId, inputId, gridSelector, veldNaam) {
    const knop = document.getElementById(knopId);
    const input = document.getElementById(inputId);
    const grid = document.querySelector(gridSelector);

    if (!knop || !input || !grid) {
        return;
    }

    function voegPlaatsToe() {
        const naam = input.value.trim();
        if (!naam) {
            return;
        }

        const bestaatAl = Array.from(grid.querySelectorAll("input[name='" + veldNaam + "']")).some(
            function (checkbox) {
                return checkbox.value.toLowerCase() === naam.toLowerCase();
            }
        );

        if (bestaatAl) {
            input.value = "";
            return;
        }

        const label = document.createElement("label");
        label.className = "checkbox";

        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.name = veldNaam;
        checkbox.value = naam;
        checkbox.checked = true;

        const span = document.createElement("span");
        span.textContent = naam;

        label.appendChild(checkbox);
        label.appendChild(span);
        grid.appendChild(label);

        input.value = "";
        input.focus();
    }

    knop.addEventListener("click", voegPlaatsToe);
    input.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
            event.preventDefault();
            voegPlaatsToe();
        }
    });
}

function initSelectieKnoppen() {
    document.querySelectorAll("[data-select-all]").forEach(function (knop) {
        knop.addEventListener("click", function () {
            const grid = document.querySelector(knop.dataset.selectAll);
            if (!grid) {
                return;
            }
            grid.querySelectorAll("input[type='checkbox']").forEach(function (checkbox) {
                checkbox.checked = true;
            });
        });
    });

    document.querySelectorAll("[data-select-none]").forEach(function (knop) {
        knop.addEventListener("click", function () {
            const grid = document.querySelector(knop.dataset.selectNone);
            if (!grid) {
                return;
            }
            grid.querySelectorAll("input[type='checkbox']").forEach(function (checkbox) {
                checkbox.checked = false;
            });
        });
    });
}

function initNieuweScanValidatie() {
    const knop = document.getElementById("nieuwe-scan-knop");
    const grid = document.getElementById("regio-grid");
    if (!knop || !grid) {
        return;
    }

    knop.addEventListener("click", function (event) {
        const geselecteerd = grid.querySelectorAll("input[name='plaats']:checked").length;
        if (geselecteerd === 0) {
            event.preventDefault();
            window.alert("Selecteer minimaal één regio voordat je een nieuwe scan start.");
        }
    });
}

function initNieuweBusinessScanValidatie() {
    const knop = document.getElementById("nieuwe-business-scan-knop");
    const regioGrid = document.getElementById("business-regio-grid");
    if (!knop || !regioGrid) {
        return;
    }

    knop.addEventListener("click", function (event) {
        const regioGeselecteerd = regioGrid.querySelectorAll("input[name='business_plaats']:checked").length;
        if (regioGeselecteerd === 0) {
            event.preventDefault();
            window.alert("Selecteer minimaal één regio voordat je een nieuwe Business-scan start.");
            return;
        }

        const categorieGeselecteerd = document.querySelectorAll("input[name='business_categorie']:checked").length;
        if (categorieGeselecteerd === 0) {
            event.preventDefault();
            window.alert("Selecteer minimaal één objectcategorie voordat je een nieuwe Business-scan start.");
        }
    });
}

function initSorteerbareTabel() {
    const tabel = document.getElementById("resultaat-tabel");
    if (!tabel) {
        return;
    }

    const headers = tabel.querySelectorAll("thead th");
    headers.forEach(function (th, index) {
        th.addEventListener("click", function () {
            sorteerTabel(tabel, index, th);
        });
    });
}

function parseBedrag(tekst) {
    if (tekst === "" || tekst === "-") {
        return null;
    }
    const negatief = tekst.trim().charAt(0) === "-";
    const cijfers = tekst.replace(/[^\d]/g, "");
    if (cijfers === "") {
        return null;
    }
    const waarde = parseInt(cijfers, 10);
    return negatief ? -waarde : waarde;
}

function parseGetal(tekst) {
    if (tekst === "" || tekst === "-") {
        return null;
    }
    const waarde = parseFloat(tekst.replace(",", "."));
    return isNaN(waarde) ? null : waarde;
}

function sorteerTabel(tabel, kolomIndex, th) {
    const tbody = tabel.querySelector("tbody");
    const rijen = Array.from(tbody.querySelectorAll("tr"));
    const oplopend = th.dataset.richting !== "asc";
    const type = th.dataset.type || "tekst";

    rijen.sort(function (rijA, rijB) {
        const waardeA = rijA.children[kolomIndex].textContent.trim();
        const waardeB = rijB.children[kolomIndex].textContent.trim();

        let vergelijking;
        if (type === "bedrag" || type === "getal") {
            const parser = type === "bedrag" ? parseBedrag : parseGetal;
            const getalA = parser(waardeA);
            const getalB = parser(waardeB);

            if (getalA === null && getalB === null) {
                vergelijking = 0;
            } else if (getalA === null) {
                vergelijking = -1;
            } else if (getalB === null) {
                vergelijking = 1;
            } else {
                vergelijking = getalA - getalB;
            }
        } else {
            vergelijking = waardeA.localeCompare(waardeB, "nl");
        }

        return oplopend ? vergelijking : -vergelijking;
    });

    tabel.querySelectorAll("thead th").forEach(function (header) {
        header.removeAttribute("data-richting");
        header.classList.remove("sort-asc", "sort-desc");
    });

    th.dataset.richting = oplopend ? "asc" : "desc";
    th.classList.add(oplopend ? "sort-asc" : "sort-desc");

    rijen.forEach(function (rij) {
        tbody.appendChild(rij);
    });
}
