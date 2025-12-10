package com.meetingoneline.meeting_one_line.auth.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.util.UUID;

public class AuthResponseDto {

    /**
     * 닉네임 중복 체크 API 응답
     */
    @Getter
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    public static class NicknameDuplicate{
        private Boolean isDuplicate;
    }

    @Getter
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    public static class Token{
        private String accessToken;
        private String refreshToken;
    }

    /**
     * 회원가입 API 응답
     */
    @Getter
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    public static class Signup{
        private String accessToken;
    }

    /**
     * 로그인 API 응답
     */
    @Getter
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    public static class Login{
        private String accessToken;
    }


    /* 재석님 */
    @Getter
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    public static class UserInfo {
        private UUID id;
        private String email;
        private String nickname;
    }

    @Getter
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    public static class Refresh {
        private String accessToken;
    }
}
