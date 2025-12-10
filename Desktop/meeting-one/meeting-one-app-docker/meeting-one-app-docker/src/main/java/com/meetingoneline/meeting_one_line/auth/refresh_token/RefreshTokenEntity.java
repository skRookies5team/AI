package com.meetingoneline.meeting_one_line.auth.refresh_token;

import com.meetingoneline.meeting_one_line.global.entity.BaseEntity;
import com.meetingoneline.meeting_one_line.user.UserEntity;
import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

@Getter
@Entity
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@Table(name = "refresh_tokens")
public class RefreshTokenEntity extends BaseEntity {
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id", nullable = false)
    private UserEntity user;

    @Column(nullable = false, length = 512)
    private String token;

    public static RefreshTokenEntity create(UserEntity user, String token){
        RefreshTokenEntity tokenEntity = new RefreshTokenEntity();
        tokenEntity.user = user;
        tokenEntity.token = token;

        return tokenEntity;
    }
}
