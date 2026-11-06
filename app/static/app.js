const $ = id => document.getElementById(id);
let offset = 0,
    busy = false,
    searchTimer;
const limit = 30;
const labels = {
    queued: 'En attente',
    running: 'En cours',
    pausing: 'Pause demandée',
    paused: 'En pause',
    completed: 'Terminée',
    partial: 'Partielle',
    failed: 'Échec',
    limited: 'Budget atteint',
    done: 'Texte prêt',
    needs_ocr: 'OCR nécessaire',
    pending: 'Extraction en attente',
    processing: 'Extraction en cours',
    unsupported: 'Original disponible, extraction non prise en charge',
    empty: 'Texte vide'
};

function node(tag, text, cls) {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = text;
    if (cls) n.className = cls;
    return n;
}

function notify(text) {
    $('message').textContent = text;
    $('message').hidden = false;
    setTimeout(() => $('message').hidden = true, 6000);
}
async function api(path, options = {}) {
    const response = await fetch(path, {
        credentials: 'same-origin',
        ...options,
        headers: {
            'Content-Type': 'application/json',
            ...(options.headers || {})
        }
    });
    if (response.status === 401) {
        $('login').hidden = false;
        $('workspace').hidden = true;
        throw new Error('Connecte-toi avec ta clé.');
    }
    if (!response.ok) {
        const data = await response.json().catch(() => ({
            detail: 'Erreur du serveur'
        }));
        throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
    }
    return response.json();
}
$('login-form').onsubmit = async event => {
    event.preventDefault();
    try {
        await api('/api/login', {
            method: 'POST',
            body: JSON.stringify({
                key: $('key').value
            })
        });
        $('key').value = '';
        await loadSources();
        await refresh();
    } catch (e) {
        notify(e.message);
    }
};
$('logout').onclick = async () => {
    await api('/api/logout', {
        method: 'POST'
    });
    location.reload();
};
$('source-form').onsubmit = async event => {
    event.preventDefault();
    const button = event.submitter;
    button.disabled = true;
    try {
        await api('/api/sources', {
            method: 'POST',
            body: JSON.stringify({
                url: $('url').value,
                respect_robots: $('robots').checked,
                max_urls: Number($('budget').value),
                mode: $('mode').value,
                extra_hosts: $('hosts').value.split(',').map(x => x.trim()).filter(Boolean)
            })
        });
        $('url').value = '';
        notify('Site ajouté à la file.');
        await loadSources();
        await refresh();
    } catch (e) {
        notify(e.message);
    } finally {
        button.disabled = false;
    }
};
async function loadSources() {
    const items = await api('/api/sources');
    const selected = $('source').value;
    $('source').replaceChildren(node('option', 'Tous les sites'));
    $('source').firstChild.value = '';
    for (const source of items) {
        const option = node('option', source.name);
        option.value = source.id;
        $('source').append(option);
    }
    if (items.some(s => s.id === selected)) $('source').value = selected;
    $('login').hidden = true;
    $('workspace').hidden = false;
    $('logout').hidden = false;
}
async function action(id, name) {
    try {
        await api(`/api/jobs/${id}/${name}`, {
            method: 'POST'
        });
        await refresh();
    } catch (e) {
        notify(e.message);
    }
}
async function loadJobs() {
    const jobs = await api('/api/jobs');
    const area = $('jobs');
    area.replaceChildren();
    if (!jobs.length) {
        area.append(node('p', 'Aucune collecte. Ajoute un lien pour commencer.', 'hint'));
        return;
    }
    for (const job of jobs) {
        const row = node('div', undefined, 'job');
        const heading = node('div', undefined, 'heading');
        heading.append(node('div', job.url, 'title'), node('span', labels[job.status] || job.status, 'badge'));
        row.append(heading);
        const c = job.counts;
        row.append(node('p', `${job.discovered} URL découvertes · ${job.documents} documents · ${c.pending||0} en attente · ${c.processing||0} en cours · ${c.failed||0} échecs`, 'hint'), node('div', job.message, 'url'));
        const actions = node('div', undefined, 'actions');
        for (const [cmd, text] of [
                ['pause', 'Pause'],
                ['resume', 'Reprendre'],
                ['retry', 'Réessayer les erreurs']
            ]) {
            const allowed = cmd === 'pause' ? ['queued', 'running'] : ['paused', 'partial', 'failed'];
            if (allowed.includes(job.status)) {
                const button = node('button', text);
                button.onclick = () => action(job.id, cmd);
                actions.append(button);
            }
        }
        if (c.failed) {
            const button = node('button', 'Voir les erreurs');
            button.onclick = async () => {
                try {
                    const errors = await api(`/api/jobs/${job.id}/errors`);
                    showPreview(errors.map(e => `${e.url}\n${e.error}`).join('\n\n'));
                } catch (e) {
                    notify(e.message);
                }
            };
            actions.append(button);
        }
        if (job.status === 'limited') row.append(node('p', 'Relance ce site avec un budget supérieur ou 0 pour élargir la couverture.', 'hint'));
        row.append(actions);
        area.append(row);
    }
}

function showPreview(text) {
    $('preview-text').textContent = text;
    $('preview').showModal();
}
$('close-preview').onclick = () => $('preview').close();
async function loadDocuments() {
    const source = $('source').value;
    const params = new URLSearchParams({
        offset,
        limit,
        q: $('search').value
    });
    if (source) params.set('source_id', source);
    const data = await api('/api/documents?' + params);
    $('count').textContent = `${data.total} ressources archivées · résultats ${data.total?offset+1:0}–${Math.min(offset+limit,data.total)}`;
    $('previous').disabled = offset === 0;
    $('next').disabled = offset + limit >= data.total;
    $('export').hidden = !source;
    if (source) $('export').href = `/api/sources/${source}/export`;
    const area = $('documents');
    area.replaceChildren();
    for (const doc of data.items) {
        const row = node('div', undefined, 'document');
        row.append(node('div', doc.title, 'title'), node('div', doc.url, 'url'), node('span', `${doc.kind.toUpperCase()} · ${Math.round(doc.size/1024)} Ko · ${labels[doc.extraction_status]||doc.extraction_status}`, 'badge'));
        const actions = node('div', undefined, 'actions');
        const link = node('a', 'Télécharger l’original', 'button');
        link.href = doc.file_url;
        actions.append(link);
        const preview = node('button', 'Lire le texte');
        preview.onclick = async () => {
            try {
                const text = await api(doc.text_url);
                showPreview(text.text || 'Aucun texte extrait.');
            } catch (e) {
                notify(e.message);
            }
        };
        actions.append(preview);
        row.append(actions);
        if (doc.extraction_error) row.append(node('p', doc.extraction_error, 'error'));
        area.append(row);
    }
}
$('source').onchange = () => {
    offset = 0;
    loadDocuments().catch(e => notify(e.message));
};
$('search').oninput = () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
        offset = 0;
        loadDocuments().catch(e => notify(e.message));
    }, 300);
};
$('previous').onclick = () => {
    offset = Math.max(0, offset - limit);
    loadDocuments().catch(e => notify(e.message));
};
$('next').onclick = () => {
    offset += limit;
    loadDocuments().catch(e => notify(e.message));
};
async function refresh() {
        if (busy) return;
        busy = true;
        try {
            await Promise.all([loadJobs(), loadDocuments()]);
            $('activity').textContent = 'Mis à jour à ' + new Date().toLocaleTimeString();
        } finally {
            busy = false;
        }
    }
    (async () => {
        try {
            await loadSources();
            await refresh();
        } catch (e) {
            if (!$('workspace').hidden) notify(e.message);
        }
    })();
setInterval(() => {
    if (!$('workspace').hidden) refresh().catch(e => {
        if (!$('workspace').hidden) notify(e.message);
    });
}, 3000);
