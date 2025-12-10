package com.meetingoneline.meeting_one_line.global.config.jwt;

import io.jsonwebtoken.*;
import jakarta.annotation.PostConstruct;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.Authentication;
import org.springframework.stereotype.Component;

import java.util.Base64;
import java.util.Date;
import java.util.List;
import java.util.UUID;

/**
 * JWT 생성 및 검증을 담당하는 Provider
 */
@Component
public class JwtTokenProvider {

    @Value("${jwt.secret}")
    private String secretKey;

    @Value("${jwt.access-expiration}")
    private long accessTokenValidity;  // 밀리초 단위
    @Value("${jwt.refresh-expiration}")
    private long refreshTokenValidity; // 밀리초 단위

    @PostConstruct
    protected void init() {
        // Base64로 인코딩된 secretKey를 디코딩하여 byte 배열로 변환
        secretKey = Base64.getEncoder().encodeToString(secretKey.getBytes());
    }

    /**
     * JWT 생성 함수
     * @param type    JwtType (ACCESS / REFRESH)
     * @return JWT 문자열
     */
    public String createToken(String userId, JwtType type) {
        Claims claims = Jwts.claims().setSubject(userId);
        Date now = new Date();
        Date validity;

        if (type == JwtType.ACCESS) {
            validity = new Date(now.getTime() + accessTokenValidity);
        } else {
            validity = new Date(now.getTime() + refreshTokenValidity);
        }

        return Jwts.builder()
                .setClaims(claims)
                .setIssuedAt(now)
                .setExpiration(validity)
                .claim("type", type.name()) // 토큰 타입 정보 저장 (선택)
                .signWith(SignatureAlgorithm.HS256, secretKey)
                .compact();
    }

    /**
     * 토큰 기반 인증 정보 생성
     * (UserId를 Authentication의 Principal로 사용)
     */
    public Authentication getAuthentication(String token) {
        UUID userId = UUID.fromString(getUserIdFromToken(token)); // ✅ 문자열 → UUID
        return new UsernamePasswordAuthenticationToken(userId, null, List.of());
    }

    /**
     * HTTP 요청 헤더에서 토큰 추출
     */
    public String resolveToken(HttpServletRequest request) {
        String bearerToken = request.getHeader("Authorization");
        if (bearerToken != null && bearerToken.startsWith("Bearer ")) {
            return bearerToken.substring(7);
        }
        return null;
    }

    /**
     * 토큰 유효성 검증
     */
    public boolean validateToken(String token) {
        try {
            Jwts.parser().setSigningKey(secretKey).parseClaimsJws(token);
            return true;
        } catch (JwtException | IllegalArgumentException e) {
            return false;
        }
    }

    /**
     * 토큰에서 userId(subject) 추출
     */
    public String getUserIdFromToken(String token) {
        return Jwts.parser().setSigningKey(secretKey)
                .parseClaimsJws(token)
                .getBody()
                .getSubject();
    }
}
