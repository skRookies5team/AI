package com.meetingoneline.meeting_one_line.global.config.jwt;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.web.filter.OncePerRequestFilter;
import java.io.IOException;

public class JwtAuthenticationFilter extends OncePerRequestFilter {

    private final JwtTokenProvider jwtTokenProvider;

    public JwtAuthenticationFilter(JwtTokenProvider jwtTokenProvider) {
        this.jwtTokenProvider = jwtTokenProvider;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain filterChain)
            throws ServletException, IOException {

        // 1. 헤더에서 토큰을 추출합니다.
        String token = jwtTokenProvider.resolveToken(request);

        // 2. 토큰이 존재하고 유효하다면 인증 정보를 SecurityContext에 저장합니다.
        if (token != null && jwtTokenProvider.validateToken(token)) {
            var authentication = jwtTokenProvider.getAuthentication(token);
            SecurityContextHolder.getContext().setAuthentication(authentication);
        }

        // 3. 다음 필터로 요청을 전달합니다.
        filterChain.doFilter(request, response);
    }

    /**
     * 인증이 필요 없는 특정 경로들은 필터링을 건너뛰도록 설정합니다.
     */
    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        String path = request.getServletPath();

        // 💡 인증이 필요 없는 API 경로들을 명시적으로 지정합니다.
        return path.startsWith("/swagger-ui")
                || path.startsWith("/v3/api-docs")
                || path.equals("/api/health")
                || path.equals("/api/auth/signup")      // 회원가입
                || path.equals("/api/auth/login")       // 로그인
                || path.equals("/api/auth/check-nickname"); // 닉네임 중복 확인
    }
}