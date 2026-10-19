import INSTALLER from './install.sh';

// This service has no bindings, secrets, database or private hub access.
export default {
    async fetch(request) {
        const url = new URL(request.url);
        if (url.pathname !== '/') return new Response('Not found', {status: 404});
        if (!['GET', 'HEAD'].includes(request.method)) {
            return new Response('Method not allowed', {status: 405, headers: {Allow: 'GET, HEAD'}});
        }
        return new Response(request.method === 'HEAD' ? null : INSTALLER, {headers: {
            'Content-Type': 'text/plain; charset=utf-8',
            'Cache-Control': 'no-store',
            'X-Content-Type-Options': 'nosniff'
        }});
    }
};
