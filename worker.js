// Cloudflare Worker — Telegram webhook → GitHub repository_dispatch 중계
// (즉시 실행 모드용, 무료 플랜으로 충분: 일 10만 요청)
//
// 환경변수 (Worker Settings → Variables):
//   GH_PAT  : GitHub fine-grained 토큰 (해당 repo만, Contents: Read and write)
//   GH_REPO : "깃허브아이디/discount-bot" 형식

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("ok"); // 헬스체크용
    }

    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("bad request", { status: 400 });
    }

    // 텍스트 메시지만 중계 (불필요한 Actions 실행 방지)
    const msg = update.message || update.edited_message;
    if (!msg || !msg.text) {
      return new Response("ok");
    }

    await fetch(`https://api.github.com/repos/${env.GH_REPO}/dispatches`, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${env.GH_PAT}`,
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "tg-discount-relay",
      },
      body: JSON.stringify({
        event_type: "telegram",
        client_payload: { update },
      }),
    });

    // Telegram에는 즉시 200 반환 (재전송 방지)
    return new Response("ok");
  },
};
